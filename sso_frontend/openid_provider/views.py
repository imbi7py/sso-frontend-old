#pylint: disable-msg=C0301
# -*- coding: utf-8 -*-
# some code from http://www.djangosnippets.org/snippets/310/ by simon
# and from examples/djopenid from python-openid-2.2.4
import urlparse
import logging
import datetime
from urllib import urlencode, quote

from django.conf import settings
from django.core.urlresolvers import reverse
from django.http import HttpResponse, HttpResponseRedirect, QueryDict
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.utils.translation import ugettext as _

from django.utils.encoding import smart_str
try:
    from django.views.decorators.csrf import csrf_exempt
except ImportError:
    from django.contrib.csrf.middleware import csrf_exempt


from login_frontend.models import BrowserLogin, add_user_log

from django.utils import timezone
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth import login as django_login
from openid.association import default_negotiator, encrypted_negotiator
from openid.consumer.discover import OPENID_IDP_2_0_TYPE, OPENID_2_0_TYPE
from openid.extensions import sreg, ax
from openid.server.server import Server, BROWSER_REQUEST_MODES
from openid.yadis.constants import YADIS_CONTENT_TYPE

from openid_provider import conf
from openid_provider.utils import add_sreg_data, add_ax_data, get_store, \
    trust_root_validation, get_trust_session_key, get_default_sreg_data
from openid_provider.models import TrustedRoot, OpenID
from django.contrib.auth.models import User as DjangoUser

logger = logging.getLogger(__name__)

@csrf_exempt
def openid_server(request):
    """
    This view is the actual OpenID server - running at the URL pointed to by 
    the <link rel="openid.server"> tag. 
    """
    logger.debug('server request %s: %s',
                 request.method, request.POST or request.GET)
    server = Server(get_store(request),
        op_endpoint=request.build_absolute_uri(reverse('openid-provider-root')))

    if not request.is_secure():
        # if request is not secure allow only encrypted association sessions
        server.negotiator = encrypted_negotiator

    # Clear AuthorizationInfo session var, if it is set
    if request.session.get('AuthorizationInfo', None):
        del request.session['AuthorizationInfo']

    querydict = dict(request.REQUEST.items())
    orequest = server.decodeRequest(querydict)
    if not orequest:
        orequest = server.decodeRequest(request.session.get('OPENID_REQUEST', None))
        if orequest:
            # remove session stored data:
            del request.session['OPENID_REQUEST']
        else:
            # not request, render info page:
            data = {
                'host': request.build_absolute_uri('/'),
                'xrds_location': request.build_absolute_uri(
                    reverse('openid-provider-xrds')),
            }
            logger.debug('invalid request, sending info: %s', data)
            if request.browser and request.browser.user:
                data["openid_identifier"] = "https://" + request.get_host() +  reverse('openid-provider-identity', args=[request.browser.user.username])
            else:
                data["page_url"] = request.build_absolute_uri()
            return render_to_response('openid_provider/server.html',
                                      data,
                                      context_instance=RequestContext(request))

    logger.debug("orequest.mode: %s" % orequest.mode)

    if orequest.mode in BROWSER_REQUEST_MODES:
        if not (request.browser and request.browser.user and request.browser.is_authenticated() and request.user.is_authenticated()):
            logger.debug('no local authentication, sending landing page')
            return landing_page(request, orequest)

        openid = openid_is_authorized(request, orequest.identity,
                                      orequest.trust_root)

        # verify return_to:
        trust_root_valid = trust_root_validation(orequest)
        validated = False

        # Allow per-url exceptions for trust roots.
        if orequest.trust_root in settings.OPENID_TRUSTED_ROOTS:
            validated = True

        if conf.FAILED_DISCOVERY_AS_VALID:
            if trust_root_valid == 'DISCOVERY_FAILED':
                validated = True
        else:
            # if in decide already took place, set as valid:
            if request.session.get(get_trust_session_key(orequest), False):
                validated = True

        if openid is not None and (validated or trust_root_valid == 'Valid'):
            id_url = request.build_absolute_uri(
                reverse('openid-provider-identity', args=[openid.openid]))
            try:
                oresponse = orequest.answer(True, identity=id_url)
            except ValueError, e:
                return render_to_response("openid_provider/error.html", {"title": "Invalid identity URL", "msg": e.message}, context_instance=RequestContext(request))
            logger.debug('orequest.answer(True, identity="%s")', id_url)
        elif orequest.immediate:
            logger.debug('checkid_immediate mode not supported')
            raise Exception('checkid_immediate mode not supported')
        else:
            request.session['OPENID_REQUEST'] = orequest.message.toPostArgs()
            request.session['OPENID_TRUSTROOT_VALID'] = trust_root_valid
            logger.debug('redirecting to decide page')
            return HttpResponseRedirect(reverse('openid-provider-decide'))
    else:
        oresponse = server.handleRequest(orequest)
    if (request.browser and request.browser.user and request.browser.is_authenticated() and request.user.is_authenticated()):
        add_sreg_data(request, orequest, oresponse)
        if conf.AX_EXTENSION:
            add_ax_data(request, orequest, oresponse)

    if (request.browser and request.browser.user and request.browser.is_authenticated() and request.user.is_authenticated()):
        # Add/update BrowserLogin object.
        (browser_login, _) = BrowserLogin.objects.get_or_create(user=request.browser.user, browser=request.browser, sso_provider="openid", signed_out=False, remote_service=str(orequest.trust_root), defaults={"auth_timestamp": timezone.now()})
        browser_login.auth_timestamp = timezone.now()
        browser_login.save()

        # Add entry to user log
        add_user_log(request, "Signed in with OpenID to %s" % orequest.trust_root, "share-square-o")

    # Convert a webresponse from the OpenID library in to a Django HttpResponse
    webresponse = server.encodeResponse(oresponse)
    logger.debug("orequest.mode: %s" % orequest.mode)
    logger.debug("webresponse.code: %s" % webresponse.code)
    if webresponse.code == 200 and orequest.mode in BROWSER_REQUEST_MODES:
        response = render_to_response('openid_provider/response.html', {
            'body': webresponse.body,
        }, context_instance=RequestContext(request))
        logger.debug('rendering browser response')
    else:
        response = HttpResponse(webresponse.body)
        response.status_code = webresponse.code
        for key, value in webresponse.headers.items():
            response[key] = value
        logger.debug('rendering raw response')
    return response

def openid_xrds(request, identity=False, id=None):
    if identity:
        types = [OPENID_2_0_TYPE]
    else:
        types = [OPENID_IDP_2_0_TYPE, sreg.ns_uri]
        if conf.AX_EXTENSION:
            types.append(ax.AXMessage.ns_uri)
    endpoints = [request.build_absolute_uri(reverse('openid-provider-root'))]
    ret = {
        'host': request.build_absolute_uri('/'),
        'types': types,
        'endpoints': endpoints,
    }
    logger.info("Options: %s" % ret)
    return render_to_response('openid_provider/xrds.xml', ret, context_instance=RequestContext(request), content_type=YADIS_CONTENT_TYPE)

def openid_decide(request):
    """
    The page that asks the user if they really want to sign in to the site, and
    lets them add the consumer to their trusted whitelist.
    # If user is logged in, ask if they want to trust this trust_root
    # If they are NOT logged in, show the landing page
    """
    server = Server(get_store(request),
	op_endpoint=request.build_absolute_uri(reverse('openid-provider-root')))
    orequest = server.decodeRequest(request.session.get('OPENID_REQUEST'))
    trust_root_valid = request.session.get('OPENID_TRUSTROOT_VALID')

    if not (request.browser and request.browser.user and request.browser.is_authenticated() and request.user.is_authenticated()):
        return landing_page(request, orequest)

    openid = openid_get_identity(request, orequest.identity)
    if openid is None:
        return error_page(
            request, "You are signed in but you don't have OpenID here!")

    if request.method == 'POST' and request.POST.get('decide_page', False):
        if request.POST.get("cancel"):
            for k in ["AuthorizationInfo", "OPENID_TRUSTROOT_VALID", "OPENID_REQUEST", get_trust_session_key(orequest)]:
                try:
                    del request.session[k]
                except KeyError:
                    pass
            return HttpResponseRedirect(reverse("login_frontend.views.indexview"))
        TrustedRoot.objects.get_or_create(
            openid=openid, trust_root=orequest.trust_root)
        if not conf.FAILED_DISCOVERY_AS_VALID:
            request.session[get_trust_session_key(orequest)] = True
        return HttpResponseRedirect(reverse('openid-provider-root'))

    return render_to_response('openid_provider/decide.html', {
        'title': _('Trust this site?'),
        'trust_root': orequest.trust_root,
        'trust_root_valid': trust_root_valid,
        'return_to': orequest.return_to,
        'identity': orequest.identity,
        'sreg': get_default_sreg_data(request, orequest),
    }, context_instance=RequestContext(request))

def error_page(request, msg):
    return render_to_response('openid_provider/error.html', {
        'title': _('Error'),
        'msg': msg,
    }, context_instance=RequestContext(request))

class SafeQueryDict(QueryDict):
    """
    A custom QueryDict class that implements a urlencode method
    knowing how to excempt some characters as safe.

    Backported from Django 1.3
    """
    def urlencode(self, safe=None):
        output = []
        if safe:
            encode = lambda k, v: '%s=%s' % ((quote(k, safe), quote(v, safe)))
        else:
            encode = lambda k, v: urlencode({k: v})
        for k, list_ in self.lists():
            k = smart_str(k, self.encoding)
            output.extend([encode(k, smart_str(v, self.encoding))
                           for v in list_])
        return '&'.join(output)

def landing_page(request, orequest, login_url=None,
                 redirect_field_name=REDIRECT_FIELD_NAME):
    """
    The page shown when the user attempts to sign in somewhere using OpenID 
    but is not authenticated with the site. For idproxy.net, a message telling
    them to log in manually is displayed.
    """
    request.session['OPENID_REQUEST'] = orequest.message.toPostArgs()
    if not login_url:
        login_url = settings.LOGIN_URL
    path = request.get_full_path()
    login_url_parts = list(urlparse.urlparse(login_url))
    if redirect_field_name:
        querystring = SafeQueryDict(login_url_parts[4], mutable=True)
        querystring[redirect_field_name] = path
        login_url_parts[4] = querystring.urlencode(safe='/')
    return HttpResponseRedirect(urlparse.urlunparse(login_url_parts))

def openid_is_authorized(request, identity_url, trust_root):
    """
    Check that they own the given identity URL, and that the trust_root is 
    in their whitelist of trusted sites.
    """
    if not (request.browser and request.browser.user and request.browser.is_authenticated() and request.user.is_authenticated()):
        return None

    openid = openid_get_identity(request, identity_url)
    if openid is None:
        return None

    if openid.trustedroot_set.filter(trust_root=trust_root).count() < 1:
        return None

    return openid

def openid_get_identity(request, identity_url):
    """
    Select openid based on claim (identity_url).
    If none was claimed identity_url will be
    'http://specs.openid.net/auth/2.0/identifier_select'
    - in that case return default one
    - if user has no default one, return any
    - in other case return None!
    """

    for openid in request.user.openid_set.iterator():
        if identity_url == request.build_absolute_uri(
                reverse('openid-provider-identity', args=[openid.openid])):
            return openid
    if identity_url == 'http://specs.openid.net/auth/2.0/identifier_select':
        # no claim was made, choose user default openid:
        openids = request.user.openid_set.filter(default=True)
        if openids.count() == 1:
            return openids[0]
        if request.user.openid_set.count() > 0:
            return request.user.openid_set.all()[0]

    (user, _) = DjangoUser.objects.get_or_create(username=request.browser.user.username, defaults={"email": request.browser.user.email, "is_staff": False, "is_active": True, "is_superuser": False, "last_login": datetime.datetime.now(), "date_joined": datetime.datetime.now()})
    user.backend = 'django.contrib.auth.backends.ModelBackend' # Horrible hack.
    django_login(request, user)
    logger.debug("Creating new OpenID association for %s", user.username)
    (openid, created) = OpenID.objects.get_or_create(user=user, openid=user.username)
    openid.save()
    return openid
