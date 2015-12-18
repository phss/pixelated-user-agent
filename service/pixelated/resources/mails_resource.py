import json
from pixelated.adapter.services.mail_sender import SMTPDownException
from pixelated.adapter.model.mail import InputMail
from twisted.web.server import NOT_DONE_YET
from pixelated.resources import respond_json_deferred
from twisted.web.resource import Resource
from twisted.web import server
from twisted.internet import defer
from twisted.python.log import err
from leap.common import events

from pixelated.utils import to_unicode


class MailsUnreadResource(Resource):
    isLeaf = True

    def __init__(self, mail_service):
        Resource.__init__(self)
        self._mail_service = mail_service

    def render_POST(self, request):
        idents = json.load(request.content).get('idents')
        deferreds = []
        for ident in idents:
            deferreds.append(self._mail_service.mark_as_unread(ident))

        d = defer.gatherResults(deferreds, consumeErrors=True)
        d.addCallback(lambda _: respond_json_deferred(None, request))
        d.addErrback(lambda _: respond_json_deferred(None, request, status_code=500))

        return NOT_DONE_YET


class MailsReadResource(Resource):
    isLeaf = True

    def __init__(self, mail_service):
        Resource.__init__(self)
        self._mail_service = mail_service

    def render_POST(self, request):
        idents = json.load(request.content).get('idents')
        deferreds = []
        for ident in idents:
            deferreds.append(self._mail_service.mark_as_read(ident))

        d = defer.gatherResults(deferreds, consumeErrors=True)
        d.addCallback(lambda _: respond_json_deferred(None, request))
        d.addErrback(lambda _: respond_json_deferred(None, request, status_code=500))

        return NOT_DONE_YET


class MailsDeleteResource(Resource):
    isLeaf = True

    def __init__(self, mail_service):
        Resource.__init__(self)
        self._mail_service = mail_service

    def render_POST(self, request):
        def response_failed(failure):
            err(failure, 'something failed')
            request.finish()

        idents = json.loads(request.content.read())['idents']
        deferreds = []
        for ident in idents:
            deferreds.append(self._mail_service.delete_mail(ident))

        d = defer.gatherResults(deferreds, consumeErrors=True)
        d.addCallback(lambda _: respond_json_deferred(None, request))
        d.addErrback(response_failed)
        return NOT_DONE_YET


class MailsRecoverResource(Resource):
    isLeaf = True

    def __init__(self, mail_service):
        Resource.__init__(self)
        self._mail_service = mail_service

    def render_POST(self, request):
        idents = json.loads(request.content.read())['idents']
        deferreds = []
        for ident in idents:
            deferreds.append(self._mail_service.recover_mail(ident))
        d = defer.gatherResults(deferreds, consumeErrors=True)
        d.addCallback(lambda _: respond_json_deferred(None, request))
        d.addErrback(lambda _: respond_json_deferred(None, request, status_code=500))
        return NOT_DONE_YET


class MailsArchiveResource(Resource):
    isLeaf = True

    def __init__(self, mail_service):
        Resource.__init__(self)
        self._mail_service = mail_service

    def render_POST(self, request):
        idents = json.loads(request.content.read())['idents']
        deferreds = []
        for ident in idents:
            deferreds.append(self._mail_service.archive_mail(ident))
        d = defer.gatherResults(deferreds, consumeErrors=True)
        d.addCallback(lambda _: respond_json_deferred({'successMessage': 'Your message was archived'}, request))
        d.addErrback(lambda _: respond_json_deferred(None, request, status_code=500))
        return NOT_DONE_YET


class MailsResource(Resource):

    def _register_smtp_error_handler(self):

        def on_error(event):
            delivery_error_mail = InputMail.delivery_error_template(delivery_address=event.content)
            self._mail_service.mailboxes.inbox.add(delivery_error_mail)

        events.register(events.catalog.SMTP_SEND_MESSAGE_ERROR, callback=on_error)

    def __init__(self, mail_service, draft_service):
        Resource.__init__(self)
        self.putChild('delete', MailsDeleteResource(mail_service))
        self.putChild('recover', MailsRecoverResource(mail_service))
        self.putChild('archive', MailsArchiveResource(mail_service))
        self.putChild('read', MailsReadResource(mail_service))
        self.putChild('unread', MailsUnreadResource(mail_service))

        self._draft_service = draft_service
        self._mail_service = mail_service
        self._register_smtp_error_handler()

    def render_GET(self, request):
        query, window_size, page = request.args.get('q')[0], request.args.get('w')[0], request.args.get('p')[0]
        unicode_query = to_unicode(query)
        d = self._mail_service.mails(unicode_query, window_size, page)

        d.addCallback(lambda (mails, total): {
            "stats": {
                "total": total,
            },
            "mails": [mail.as_dict() for mail in mails]
        })
        d.addCallback(lambda res: respond_json_deferred(res, request))

        def error_handler(error):
            print error

        d.addErrback(error_handler)

        return NOT_DONE_YET

    def render_POST(self, request):
        def onError(error):
            if isinstance(error.value, SMTPDownException):
                respond_json_deferred({'message': str(error.value)}, request, status_code=503)
            else:
                err(error, 'something failed')
                respond_json_deferred({'message': 'an error occurred while sending'}, request, status_code=422)

        deferred = self._handle_post(request)
        deferred.addErrback(onError)

        return server.NOT_DONE_YET

    def render_PUT(self, request):
        def onError(error):
                err(error, 'error saving draft')
                respond_json_deferred("", request, status_code=422)

        deferred = self._handle_put(request)
        deferred.addErrback(onError)

        return server.NOT_DONE_YET

    @defer.inlineCallbacks
    def _fetch_attachment_contents(self, attachments):
        for attachment in attachments:
            retrieved_attachment = yield self._mail_service.attachment(attachment['id'])
            attachment['raw'] = retrieved_attachment['content']

    @defer.inlineCallbacks
    def _handle_post(self, request):
        content_dict = json.loads(request.content.read())
        self._fetch_attachment_contents(content_dict.get('attachments', []))

        sent_mail = yield self._mail_service.send_mail(content_dict)
        respond_json_deferred(sent_mail.as_dict(), request)

    @defer.inlineCallbacks
    def _handle_put(self, request):
        content_dict = json.loads(request.content.read())
        self._fetch_attachment_contents(content_dict.get('attachments', []))

        _mail = InputMail.from_dict(content_dict)
        draft_id = content_dict.get('ident')
        pixelated_mail = yield self._draft_service.process_draft(draft_id, _mail)

        if not pixelated_mail:
            respond_json_deferred("", request, status_code=422)
        else:
            respond_json_deferred({'ident': pixelated_mail.ident}, request)
