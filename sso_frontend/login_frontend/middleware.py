#pylint: disable-msg=C0301

"""
Middleware classes.

BrowserMiddleware adds request.browser, and automatically signs user out,
if browser was restarted, and not saved.

Also, session cookie is automatically added if it does not exist yet.
"""

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import MiddlewareNotUsed
from django.http import HttpResponse
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.utils import timezone
from login_frontend.models import Browser, BrowserUsers, BrowserLogin, create_browser_uuid, BrowserP0f
from login_frontend.providers import pubtkt_logout
from login_frontend.utils import dedup_messages
import datetime
import logging
import p0f
import pytz
import re
import socket
import time
from django_statsd.clients import statsd as sd
from django.core.cache import get_cache

dcache = get_cache("default")

log = logging.getLogger(__name__)

timing_log = logging.getLogger("request_timing")
p0f_log = logging.getLogger("p0f")


DISALLOWED_UA = [
 re.compile("^Wget/.*"),
 re.compile("^Pingdom.com_bot_version.*"),
 re.compile("^curl/.*"),
 re.compile("^nutch-.*"),
]

__all__ = ["get_browser", "BrowserMiddleware", "get_browser_instance", "P0fMiddleware"]

@sd.timer("get_browser_instance")
def get_browser_instance(request):    
    bid = request.COOKIES.get(Browser.C_BID)
    if not bid:
        return None
    try:
        browser = Browser.objects.select_related("user").get(bid=bid)
        sd.incr("get_browser_instance.success", 1)
    except Browser.DoesNotExist:
        sd.incr("get_browser_instance.invalid", 1)
        log.info("Unknown browser id '%s' from '%s'", bid, request.META.get("REMOTE_ADDR"))
        return None

    return browser

@sd.timer("get_browser")
def get_browser(request):
    browser = get_browser_instance(request)
    if browser is None:
        return None
    bid = browser.bid_public

    if request.path.startswith("/csp-report") or request.path.startswith("/timesync"):
        log.debug("Browser '%s' from '%s' reporting CSP/timesync - skip sign-out processing", bid, request.META.get("REMOTE_ADDR"))
        sd.incr("get_browser.skip", 1)
        return browser

    if request.COOKIES.get(Browser.C_BID_SESSION) == browser.bid_session:
        browser.valid_session_bid = True
    else:
        browser.valid_session_bid = False
        # Mark session based logins as signed_out
        sessions = BrowserLogin.objects.filter(browser=browser).filter(expires_session=True).filter(signed_out=False)
        for session in sessions:
            log.info("Marking session %s for %s (user %s) as signed out, after browser session id cookie disappeared.", session.sso_provider, browser.bid, session.user.username)
            session.signed_out = True
            session.save()
        if not browser.save_browser:
            # Browser was restarted, and save_browser is not set. Logout.
            log.info("Browser bid_public=%s was restarted. Logging out. path: %s", browser.bid_public, request.path)
            sd.incr("get_browser.browser_restart", 1)
            dedup_messages(request, messages.INFO, "According to our records, your browser was restarted. Therefore, you were signed out. If this is your own computer, you can avoid this by checking 'Remember this browser' below the sign-in form.")
            browser.logout(request)

    if browser.user:
        r_k = "browser-location-last-update-%s-%s" % (browser.user.username, browser.bid_public)
        last_update = dcache.get(r_k)
        remote_address = request.META.get("REMOTE_ADDR")
        if last_update != remote_address:
            user_to_browser, _ = BrowserUsers.objects.get_or_create(user=browser.user, browser=browser)
            if request.path.startswith("/ping"):
                sd.incr("get_browser.passive_access", 1)
                user_to_browser.remote_ip_passive = remote_address
                user_to_browser.last_seen_passive = timezone.now()
            else:
                sd.incr("get_browser.active_access", 1)
                user_to_browser.remote_ip = remote_address
                user_to_browser.last_seen = timezone.now()
            user_to_browser.save()
            dcache.set(r_k, remote_address, 30)
    return browser


class P0fMiddleware(object):
    def __init__(self):
        if settings.P0F_SOCKET is None:
            raise MiddlewareNotUsed

    @sd.timer("P0fMiddleware.process_request")
    def process_request(self, request):
        if request.path.startswith("/timesync"):
            return

        if not hasattr(request, "browser") or not request.browser:
            return
        browser = request.browser
        remote_addr = request.META.get("REMOTE_ADDR")
        r_k = "p0f-last-update-%s" % (browser.bid_public)
        last_update = dcache.get(r_k)
        if last_update == remote_addr:
            return
        dcache.set(r_k, remote_addr, 30)

        def update_newest(newest, remote_info):
            if remote_info["uptime_sec"] == None and newest.uptime_sec == None:
                # If both old and new uptimes are None, don't create a new instance.
                return True
            if remote_info["uptime_sec"] == None:
                return False
            if newest.uptime_sec == None:
                return False

            time_since_last = timezone.now() - newest.updated_at
            time_since_last_sec = time_since_last.days * 86400 + time_since_last.seconds

            expected_uptime = newest.uptime_sec + time_since_last_sec

            up_mod_days = remote_info["up_mod_days"]
            if up_mod_days and up_mod_days > datetime.timedelta(days=1):
                # Detect wraparound
                up_mod_sec = up_mod_days.days * 86400
              
                if expected_uptime > up_mod_sec:
                    log.debug("p0f: %s@%s - uptime wraparound detected: %s", browser.bid_public, remote_addr, expected_uptime)
                    newest.wraparounds += 1
                    expected_uptime -= up_mod_sec

            uptime_diff = expected_uptime - remote_info["uptime_sec"]
            allowed_diff = max(10*60, expected_uptime * 0.1)

            if uptime_diff > allowed_diff:
                log.debug("p0f: %s@%s - uptime went backwards %s seconds", browser.bid_public, remote_addr, uptime_diff)
                return False

            if uptime_diff < -allowed_diff:
                log.debug("p0f: %s@%s - uptime jumped onwards %s seconds", browser.bid_public, remote_addr, uptime_diff)
                return False

            time_since_last = timezone.now() - newest.last_seen

            update_keys = ("total_conn", "uptime_sec", "os_flavor", "os_name", "os_match_q", "distance", "last_seen")
            for k in update_keys:
                setattr(newest, k, remote_info[k])
            log.debug("p0f: updated %s", browser.bid_public)
            newest.save()
            return True

        try:
            p0fapi = p0f.P0f(settings.P0F_SOCKET)
            try:
                sd.incr("p0f.queried", 1)
                remote_info = p0fapi.get_info(remote_addr)
                sd.incr("p0f.fetched", 1)
            except KeyError, e:
                # No information exists.
                sd.incr("p0f.error.no_info", 1)
                log.debug("p0f: %s", str(e))
                return
            except (ValueError, p0f.P0fException), e:
                # Invalid information received from p0f
                sd.incr("p0f.error.invalid", 1)
                log.error("p0f raised KeyError: %s", str(e))
                return

            username = None
            if browser.user:
                username = browser.user.username

            p0f_log.info("%s - %s - %s - %s - %s", remote_addr, browser.bid_public, username, request.path, str(remote_info))

            if remote_info["last_nat"] != None:
                # NAT detected. Don't store/update anything.
                sd.incr("p0f.nat", 1)
                log.debug("p0f: %s@%s - NAT detected", browser.bid_public, remote_addr)
                return

            try:
                newest = BrowserP0f.objects.filter(browser=browser).latest()
            except BrowserP0f.DoesNotExist:
                newest = None

            updated = False
            if newest:
                # Update this if uptime_diff matches, otherwise create a new object.
                updated = update_newest(newest, remote_info)
                log.debug("p0f: %s@%s - update_newest returned %s", browser.bid_public, remote_addr, updated)

            if not updated:
                data = {"browser": browser}
                for k in ("first_seen", "last_seen", "total_conn", "uptime_sec", "last_nat", "distance", "os_match_q", "os_name", "os_flavor", "link_type"):
                    data[k] = remote_info[k]
                if remote_info["up_mod_days"]:
                    data["up_mod_days"] = remote_info["up_mod_days"].days
                else:
                    data["up_mod_days"] = None

                log.info("p0f: creating new log entry for %s@%s: uptime %s", browser.bid_public, remote_addr, data["uptime_sec"])
                BrowserP0f.objects.create(**data)

        except socket.error, e:
             sd.incr("p0f.error.socket", 1)
             log.error("p0f raised socket.error: %s" % e)

class BrowserMiddleware(object):
    """ Adds request.browser. """ 

    @sd.timer("BrowserMiddleware.process_request")
    def process_request(self, request):
        """ Adds request.browser. Filters out monitoring bots. """
        ua = request.META.get("HTTP_USER_AGENT") 
        for ua_re in DISALLOWED_UA:
            if ua_re.match(ua):
                ret = {}
                try:
                    (_, ret["admin"]) = settings.ADMINS[0]
                except (IndexError, ValueError):
                    pass
                return render_to_response("login_frontend/errors/you_are_a_bot.html", ret, context_instance=RequestContext(request))

        request.browser = get_browser(request)


    @sd.timer("BrowserMiddleware.process_response")
    def process_response(self, request, response):
        """ Automatically adds session cookie if old one is not available. """
        response["Server"] = "https://github.com/ojarva/sso-frontend"
        if request.path.startswith("/csp-report") or request.path.startswith("/timesync"):
            log.debug("Browser from '%s' reporting CSP/timesync - skip process_response", request.META.get("REMOTE_ADDR"))
            sd.incr("login_frontend.middleware.BrowserMiddleware.process_response.skip", 1)
            return response
        
        # Browser from process_request is not available here.
        browser = get_browser_instance(request)

        if not browser or browser.get_auth_level() < Browser.L_STRONG:
            response = pubtkt_logout(request, response)

        if not browser:
            log.debug("Browser does not exist")
            return response

        cookies = browser.get_cookies()
        if request.COOKIES.get(Browser.C_BID_SESSION) != browser.bid_session:
            # No valid session ID exists. Regen it first.
            browser.bid_session = create_browser_uuid()
            browser.save()
            log.info("Session bid does not exist. Regenerating. bid_public=%s, bid_session=%s" % (browser.bid_public, browser.bid_session))
            cookies = browser.get_cookies()

        if request.COOKIES.get(Browser.C_BID_PUBLIC) != browser.bid_public:
            # Public bid does not match. Set it again.
            cookies = browser.get_cookies()

        for cookie_name, cookie in cookies:
            log.debug("Setting cookie %s=%s for %s at %s" % (cookie_name, cookie, browser.bid_public, request.path))
            response.set_cookie(cookie_name, **cookie)
        return response


class TimesyncMiddleware(object):
    @sd.timer("TimesyncMiddleware.process_request")
    def process_request(self, request):
        request.should_timesync = False
        bid_public = request.COOKIES.get(Browser.C_BID_PUBLIC)
        if not bid_public:
            return
        last_timesync = dcache.get("timesync-at-%s" % bid_public)
        if not last_timesync:
            request.should_timesync

def log_request_timing(phase, request):
    timing_log.info("%s: %.5f - %s - %s - [%s] - [bid_public=%s]", phase, time.time(), request.META.get("REMOTE_ADDR"), request.get_full_path(), request.META.get("HTTP_USER_AGENT"), request.COOKIES.get(Browser.C_BID_PUBLIC))


class InLoggingMiddleware(object):
    def __init__(self):
        if settings.DISABLE_TIMING_LOGGING:
            raise MiddlewareNotUsed

    def process_request(self, request):
        log_request_timing("process_request.first", request)

class ViewLoggingMiddleware(object):
    def __init__(self):
        if settings.DISABLE_TIMING_LOGGING:
            raise MiddlewareNotUsed

    def process_view(self, request, view_func, view_args, view_kwargs):
        log_request_timing("process_view.last", request)

class OutLoggingMiddleware(object):
    def __init__(self):
        if settings.DISABLE_TIMING_LOGGING:
            raise MiddlewareNotUsed

    def process_response(self, request, response):
        log_request_timing("process_response.last", request)
        return response
