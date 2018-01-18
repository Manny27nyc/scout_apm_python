from __future__ import absolute_import

from django.core import urlresolvers
from django.core.handlers.base import BaseHandler
from django.core.handlers.wsgi import WSGIHandler
from django.urls import resolvers

from apm.monkey import monkeypatch_method, CallableProxy
from apm.stacktracer import trace_function
from apm.tracked_request import TrackedRequest
import re

import pdb

import traceback

def patch_function_list(functions, action_type, format_string):
    for i, func in enumerate(functions):
        if hasattr(func, 'im_class'):
            middleware_name = func.im_class.__name__
        else:
            middleware_name = func.__name__
        info = (action_type, {"name": middleware_name})
        functions[i] = trace_function(func, info)


def wrap_middleware_with_tracers(request_handler):
    # XXX: Figure out why request middleware isn't getting instrumented
    patch_function_list(request_handler._request_middleware, 'Middleware/Request', 'Middleware: %s (request)')
    patch_function_list(request_handler._view_middleware, 'Middleware/View', 'Middleware: %s (view)')
    patch_function_list(request_handler._template_response_middleware, 'Middleware/Template/Response', 'Middleware: %s (template response)')
    patch_function_list(request_handler._response_middleware, 'Middleware/Response', 'Middleware: %s (response)')
    patch_function_list(request_handler._exception_middleware, 'Middleware/Exception', 'Middleware: %s (exeption)')

# The linter thinks the methods we monkeypatch are not used
# pylint: disable=W0612
middleware_patched = False
def intercept_middleware():
    @monkeypatch_method(WSGIHandler)
    def __call__(original, self, *args, **kwargs):
        # The middleware cache may have been built before we have a chance to monkey patch
        # it, so do so here
        global middleware_patched
        if not middleware_patched and self._request_middleware is not None:
            self.initLock.acquire()
            try:
                if not middleware_patched:
                    wrap_middleware_with_tracers(self)
                    middleware_patched = True
            finally:
                self.initLock.release()
        return original(*args, **kwargs)

    @monkeypatch_method(BaseHandler)
    def load_middleware(original, self, *args, **kwargs):
        global middleware_patched
        original(*args, **kwargs)
        wrap_middleware_with_tracers(self)
        middleware_patched = True

def intercept_resolver_and_view():
    # The only way we can really wrap the view method is by replacing the implementation
    # of RegexURLResolver.resolve. It would be nice if django had more configurability here, but it does not.
    # However, we only want to replace it when invoked directly from the request handling stack, so we
    # inspect the callstack in __new__ and return either a normal object, or an instance of our proxying
    # class.
    # XXX: Check against other djangos - `urlresolvers` from django.core vs the `urls.resolvers`
    real_resolver_cls = resolvers.RegexURLResolver

    class ProxyRegexURLResolverMetaClass(resolvers.RegexURLResolver.__class__):
        def __instancecheck__(self, instance):
            # Some places in django do a type check against RegexURLResolver and behave differently based on the result, so we have to
            # make sure the replacement class we plug in accepts instances of both the default and replaced types.
            return isinstance(instance, real_resolver_cls) or super(ProxyRegexURLResolverMetaClass, self).__instancecheck__(instance)

    class ProxyRegexURLResolver(object):
        __metaclass__ = ProxyRegexURLResolverMetaClass

        def __new__(cls, *args, **kwargs):
            real_object = real_resolver_cls(*args, **kwargs)
            obj = super(ProxyRegexURLResolver, cls).__new__(cls)
            obj.other = real_object
            return obj
            # XXX: this return is behind an if statement in speedbar, sometimes doesn't instrument. unsure why

        def __getattr__(self, attr):
            return getattr(self.other, attr)

        def resolve(self, path):
            callbacks = self.other.resolve(path)
            callbacks.func = self.trace_view_function(callbacks.func, ('Controller', {"path": path, "name": callbacks._func_path}))
            return callbacks

        # XXX: Can this maybe be a callback that takes the span, and the req and the args?
        # Rather than a full reimpl of this?
        def trace_view_function(self, func, info):
            try:
                def tracing_function(original, *args, **kwargs):
                    entry_type, detail = info

                    operation = entry_type
                    if detail["name"] is not None:
                        operation = operation + "/" + detail["name"]

                    span = TrackedRequest.instance().start_span(operation=operation)
                    for key in detail:
                        span.note(key, detail[key])

                    # And the custom View stuff
                    request = args[0]

                    # Extract headers
                    regex = re.compile('^HTTP_')
                    headers = dict((regex.sub('', header), value) for (header, value)
                                   in request.META.items() if header.startswith('HTTP_'))

                    span.note("remote_addr", request.META["REMOTE_ADDR"])

                    try:
                        return original(*args, **kwargs)
                    finally:
                        TrackedRequest.instance().stop_span()
                        print(span.dump())

                return CallableProxy(func, tracing_function)
            except Exception as err:
                print(err)
                # If we can't wrap for any reason, just return the original
                return func

    resolvers.RegexURLResolver = ProxyRegexURLResolver

class ViewInstrument:
    @staticmethod
    def install():
        intercept_middleware()
        intercept_resolver_and_view()
        print("Monkey patched View")

