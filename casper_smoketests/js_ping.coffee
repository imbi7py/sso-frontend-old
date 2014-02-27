phantom.clearCookies()

casper.userAgent('Mozilla/4.0 (compatible; MSIE 6.0; Windows NT 5.1)');
casper.start 'http://localhost:8000', ->
   @.then ->
    @.test.assertHttpStatus(200);
    @.test.assertUrlMatch 'http://localhost:8000/first/password?_sso=internal&next=http://localhost:8000/index'
   @.thenOpen("http://localhost:8000/ping/internal/js")
   @.then ->
    @.test.assertHttpStatus(200);
    @.test.assertUrlMatch "http://localhost:8000/ping/internal/js"

   @.thenOpen("http://localhost:8000")
   @.viewport(1200, 1200);
   @.then ->
    @.fill("form[name='loginform']", {
     "username": "test_valid",
     "password": "testpassword"
    }, true);
   @.then ->
    @.test.assertHttpStatus(200);
    @.test.assertUrlMatch 'http://localhost:8000/second/sms?_sso=internal&next=http://localhost:8000/index'
   @.then ->
    @.fill("form[name='loginform']", {
     "otp": "12345"
    }, true);
   @.then ->
    @.test.assertUrlMatch 'http://localhost:8000/configure?_sso=internal&next=http://localhost:8000/index'
    @.test.assertHttpStatus(200)
   @.thenOpen("http://localhost:8000/ping/internal/js")
   @.then ->
    @.test.assertHttpStatus(200);
    @.test.assertUrlMatch "http://localhost:8000/ping/internal/js"

casper.run ->
  @.test.done()
