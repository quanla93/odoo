from datetime import timedelta
from unittest.mock import Mock, patch

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMailQueue(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Queue = self.env['bhsoft.mail.queue']

    def _queue(self, **values):
        defaults = {
            'name': 'Recipient',
            'email_to': 'Test@Example.com ',
            'subject': 'Subject',
            'body_html': 'Body',
        }
        defaults.update(values)
        return self.Queue.create(defaults)

    def test_email_is_normalized(self):
        queue = self._queue()
        self.assertEqual(queue.normalized_email, 'test@example.com')

    def test_claim_changes_status_once(self):
        first = self._queue(email_to='first@example.com')
        second = self._queue(email_to='second@example.com')

        claimed = self.Queue._claim_pending_records(limit=1)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed.status, 'processing')

        next_claimed = self.Queue._claim_pending_records(limit=10)
        self.assertEqual(next_claimed, (first | second) - claimed)

    def test_retry_only_failed(self):
        failed = self._queue(email_to='failed@example.com', status='failed')
        bounced = self._queue(email_to='bounced@example.com', status='bounced')
        self.assertTrue(failed.terminal_at)
        self.assertTrue(bounced.terminal_at)
        failed.action_retry()
        bounced.action_retry()
        self.assertEqual(failed.status, 'none')
        self.assertFalse(failed.terminal_at)
        self.assertEqual(bounced.status, 'bounced')

    def test_claim_skips_active_suppression(self):
        record = self._queue(email_to='suppressed@example.com')
        suppression = self.env['bhsoft.email.suppression'].create({
            'normalized_email': ' SUPPRESSED@EXAMPLE.COM ',
            'reason': 'manual',
        })

        claimed = self.Queue._claim_pending_records(record_ids=record.ids)

        self.assertFalse(claimed)
        self.assertEqual(record.status, 'skipped')
        self.assertEqual(record.suppression_id, suppression)
        self.assertTrue(record.terminal_at)

    def test_claim_invalidates_cached_suppression_fields(self):
        record = self._queue(email_to='cached@example.com')
        self.assertEqual(record.status, 'none')
        self.assertFalse(record.suppression_id)
        suppression = self.env['bhsoft.email.suppression'].create({
            'normalized_email': record.email_to,
            'reason': 'manual',
        })

        self.Queue._claim_pending_records(record_ids=record.ids)

        self.assertEqual(record.status, 'skipped')
        self.assertEqual(record.suppression_id, suppression)

    def test_pre_send_suppression_does_not_advance_schedule(self):
        blocked = self._queue(email_to='blocked@example.com')
        allowed = self._queue(email_to='allowed@example.com')
        claimed = self.Queue._claim_pending_records(
            limit=2, record_ids=(blocked | allowed).ids,
        )
        self.env['bhsoft.email.suppression'].create({
            'normalized_email': blocked.email_to,
            'reason': 'manual',
        })
        config = self.env['ir.config_parameter'].sudo()
        config.set_str('bhs_sendgrid_mailer.api_key', 'test-key')
        config.set_str('bhs_sendgrid_mailer.sender_email', 'sender@example.com')
        config.set_int('bhs_sendgrid_mailer.delay_min', 45)
        config.set_int('bhs_sendgrid_mailer.delay_max', 45)
        cursor = fields.Datetime.now() + timedelta(minutes=10)

        with patch.object(
            type(allowed), '_create_sendgrid_batch_id', return_value='batch-1',
        ) as create_batch, patch.object(
            type(allowed), '_send_via_sendgrid_api', return_value='message-1',
        ) as send:
            self.Queue._process_send_records(claimed, schedule_cursor=cursor)

        self.assertEqual(blocked.status, 'skipped')
        self.assertFalse(blocked.scheduled_send_time)
        self.assertEqual(allowed.scheduled_send_time, cursor + timedelta(seconds=45))
        create_batch.assert_called_once()
        send.assert_called_once()

    def test_manual_send_counts_claim_suppression_as_skipped(self):
        blocked = self._queue(email_to='blocked@example.com')
        allowed = self._queue(email_to='allowed@example.com')
        self.env['bhsoft.email.suppression'].create({
            'normalized_email': blocked.email_to,
            'reason': 'manual',
        })

        with patch.object(
            type(allowed), '_process_send_records', autospec=True,
        ) as process:
            process.side_effect = lambda _self, records, **kwargs: records.write({
                'status': 'queued',
                'scheduled_send_time': fields.Datetime.now() + timedelta(minutes=1),
            })
            action = (blocked | allowed).action_send_now()

        self.assertEqual(blocked.status, 'skipped')
        self.assertEqual(allowed.status, 'queued')
        self.assertIn('Submitted: 1', action['params']['message'])
        self.assertIn('Deferred to a later batch: 0', action['params']['message'])
        self.assertIn('Already processed or not sendable: 1', action['params']['message'])

    def test_manual_send_does_not_count_failed_processing_as_submitted(self):
        record = self._queue(email_to='failed@example.com')

        with patch.object(
            type(record), '_process_send_records', autospec=True,
        ) as process:
            process.side_effect = lambda _self, records, **kwargs: records.write({
                'status': 'failed',
                'error_message': 'Provider failure',
            })
            action = record.action_send_now()

        self.assertEqual(record.status, 'failed')
        self.assertIn('Submitted: 0', action['params']['message'])
        self.assertIn('Deferred to a later batch: 0', action['params']['message'])
        self.assertIn('Already processed or not sendable: 1', action['params']['message'])

    def test_manual_send_caps_selected_pending_records_to_batch_size(self):
        records = self.Queue.create([
            {
                'name': f'Recipient {index}',
                'email_to': f'user{index}@example.com',
                'subject': 'Subject',
                'body_html': 'Body',
            }
            for index in range(35)
        ])
        self.env['ir.config_parameter'].sudo().set_int(
            'bhs_sendgrid_mailer.batch_size', 30,
        )

        with patch.object(type(records), '_process_send_records', autospec=True) as process:
            records.action_send_now()

        submitted = process.call_args.args[1]
        records.invalidate_recordset(['status'])
        self.assertEqual(len(submitted), 30)
        self.assertEqual(len(records.filtered(lambda record: record.status == 'processing')), 30)
        self.assertEqual(len(records.filtered(lambda record: record.status == 'none')), 5)

    def test_future_schedule_is_used_as_next_batch_cursor(self):
        active = self._queue(
            email_to='active@example.com',
            status='queued',
            scheduled_send_time=fields.Datetime.now() + timedelta(minutes=10),
        )
        pending = self._queue(email_to='pending@example.com')

        with patch.object(type(pending), '_process_send_records', autospec=True) as process:
            result = pending._dispatch_mail_batch(record_ids=pending.ids)

        self.assertFalse(result['reason'])
        self.assertEqual(result['active_until'], active.scheduled_send_time)
        self.assertEqual(pending.status, 'processing')
        self.assertEqual(
            process.call_args.kwargs['schedule_cursor'],
            active.scheduled_send_time,
        )

    def test_expired_schedule_starts_next_batch_from_now(self):
        expired = self._queue(
            email_to='expired@example.com',
            status='queued',
            scheduled_send_time=fields.Datetime.now() - timedelta(seconds=1),
        )
        pending = self._queue(email_to='pending@example.com')

        with patch.object(type(pending), '_process_send_records', autospec=True) as process:
            result = pending._dispatch_mail_batch(record_ids=pending.ids)

        self.assertFalse(result['reason'])
        self.assertEqual(result['records'], pending)
        self.assertEqual(pending.status, 'processing')
        self.assertEqual(
            process.call_args.kwargs['schedule_cursor'],
            expired.scheduled_send_time,
        )

    def test_busy_batch_lock_does_not_claim_records(self):
        pending = self._queue(email_to='pending@example.com')

        with patch.object(
            type(pending), '_acquire_mail_batch_lock', return_value=False,
        ), patch.object(type(pending), '_process_send_records', autospec=True) as process:
            result = pending._dispatch_mail_batch(record_ids=pending.ids)

        self.assertEqual(result['reason'], 'busy')
        self.assertEqual(pending.status, 'none')
        process.assert_not_called()

    def test_invalid_delay_fails_claimed_records_without_provider_call(self):
        record = self._queue()
        config = self.env['ir.config_parameter'].sudo()
        config.set_int('bhs_sendgrid_mailer.delay_min', 90)
        config.set_int('bhs_sendgrid_mailer.delay_max', 45)
        claimed = self.Queue._claim_pending_records(record_ids=record.ids)

        with patch(
            'odoo.addons.bhs_sendgrid_mailer.models.mail_queue.requests.post'
        ) as post:
            self.Queue._process_send_records(claimed)

        self.assertEqual(record.status, 'failed')
        post.assert_not_called()

    def test_sendgrid_receives_exact_plain_text_body(self):
        body = 'Hello <Alice> & team,\n\nFirst line.\nSecond line.\n\nRegards,\nBHSoft'
        record = self._queue(body_html=body)
        response = type('Response', (), {
            'status_code': 202,
            'headers': {'X-Message-Id': 'message-1'},
            'text': '',
        })()

        with patch(
            'odoo.addons.bhs_sendgrid_mailer.models.mail_queue.requests.post',
            return_value=response,
        ) as post:
            message_id = record._send_via_sendgrid_api(
                record,
                'test-key',
                'sender@example.com',
            )

        payload = post.call_args.kwargs['json']
        self.assertEqual(message_id, 'message-1')
        self.assertEqual(payload['content'][0]['type'], 'text/plain')
        self.assertEqual(payload['content'][0]['value'], body)
        self.assertEqual(record.body_html, body)

    def test_processing_appends_after_schedule_cursor(self):
        records = self.Queue.create([
            {
                'name': f'Recipient {index}',
                'email_to': f'cursor{index}@example.com',
                'subject': 'Subject',
                'body_html': 'Body',
            }
            for index in range(2)
        ])
        claimed = self.Queue._claim_pending_records(
            limit=2,
            record_ids=records.ids,
        )
        cursor = fields.Datetime.now() + timedelta(minutes=10)
        config = self.env['ir.config_parameter'].sudo()
        config.set_str('bhs_sendgrid_mailer.api_key', 'test-key')
        config.set_str('bhs_sendgrid_mailer.sender_email', 'sender@example.com')
        config.set_int('bhs_sendgrid_mailer.delay_min', 45)
        config.set_int('bhs_sendgrid_mailer.delay_max', 45)

        with patch.object(
            type(records), '_create_sendgrid_batch_id', return_value='batch-1',
        ), patch.object(
            type(records), '_send_via_sendgrid_api', return_value='message-1',
        ):
            self.Queue._process_send_records(claimed, schedule_cursor=cursor)

        records.invalidate_recordset(['scheduled_send_time'])
        scheduled_times = sorted(records.mapped('scheduled_send_time'))
        self.assertEqual(scheduled_times[0], cursor + timedelta(seconds=45))
        self.assertEqual(scheduled_times[1], cursor + timedelta(seconds=90))

    def test_direct_sendgrid_does_not_create_odoo_mail(self):
        body = 'Hello <Alice> & team,\n\nFirst line.\nSecond line.'
        record = self._queue(body_html=body)
        claimed = self.Queue._claim_pending_records(record_ids=record.ids)
        config = self.env['ir.config_parameter'].sudo()
        config.set_str('bhs_sendgrid_mailer.api_key', 'test-key')
        config.set_str('bhs_sendgrid_mailer.sender_email', 'sender@example.com')
        config.set_int('bhs_sendgrid_mailer.delay_min', 0)
        config.set_int('bhs_sendgrid_mailer.delay_max', 0)

        with patch.object(
            type(record), '_create_sendgrid_batch_id', return_value='batch-1',
        ), patch.object(
            type(record), '_send_via_sendgrid_api', return_value='message-1',
        ):
            self.Queue._process_send_records(claimed)

        self.assertEqual(record.status, 'queued')
        self.assertEqual(record.body_html, body)
        self.assertFalse(record.mail_id)

    def test_smtp_fallback_creates_odoo_mail_with_html_body(self):
        body = 'Hello <Alice> & team,\n\nFirst line.\nSecond line.'
        record = self._queue(body_html=body)
        claimed = self.Queue._claim_pending_records(record_ids=record.ids)
        config = self.env['ir.config_parameter'].sudo()
        config.set_str('bhs_sendgrid_mailer.api_key', '')
        config.set_int('bhs_sendgrid_mailer.delay_min', 0)
        config.set_int('bhs_sendgrid_mailer.delay_max', 0)

        self.Queue._process_send_records(claimed)

        self.assertEqual(record.status, 'queued')
        self.assertEqual(record.body_html, body)
        self.assertIn('&lt;Alice&gt; &amp; team', record.mail_id.body_html)
        self.assertIn('<p>First line.<br/>Second line.</p>', record.mail_id.body_html)

    def test_sendgrid_timeout_requires_reconciliation(self):
        record = self._queue()
        config = self.env['ir.config_parameter'].sudo()
        config.set_str('bhs_sendgrid_mailer.api_key', 'test-key')
        config.set_str('bhs_sendgrid_mailer.sender_email', 'sender@example.com')
        config.set_int('bhs_sendgrid_mailer.delay_min', 0)
        config.set_int('bhs_sendgrid_mailer.delay_max', 0)
        claimed = self.Queue._claim_pending_records(record_ids=record.ids)

        with patch.object(type(record), '_create_sendgrid_batch_id', return_value='batch-1'), patch(
            'odoo.addons.bhs_sendgrid_mailer.models.mail_queue.requests.post',
            side_effect=__import__('requests').Timeout('timed out'),
        ):
            self.Queue._process_send_records(claimed)

        self.assertEqual(record.status, 'unknown')
        self.assertEqual(record.sendgrid_batch_id, 'batch-1')
        self.assertTrue(record.scheduled_send_time)
        record.action_retry()
        self.assertEqual(record.status, 'unknown')

    def test_direct_sendgrid_status_sync_uses_scheduled_time(self):
        scheduled_time = fields.Datetime.now() - timedelta(seconds=1)
        record = self._queue(
            status='queued',
            scheduled_send_time=scheduled_time,
            sendgrid_msg_id='message-1',
        )

        record.action_sync_status()

        self.assertEqual(record.status, 'sent')
        self.assertEqual(record.actual_sent_time, scheduled_time)
        self.assertTrue(record.terminal_at)

    def test_unsubscribe_event_suppresses_and_marks_terminal(self):
        record = self._queue(status='queued')

        record._apply_sendgrid_event('group_unsubscribe', {
            'sg_event_id': 'unsubscribe-1',
            'timestamp': 1_758_621_000,
            'email': record.email_to,
        })

        self.assertEqual(record.status, 'unsubscribed')
        self.assertEqual(record.suppression_id.reason, 'unsubscribe')
        self.assertTrue(record.terminal_at)

    def test_suppression_enforcement_cancels_future_batch(self):
        record = self._queue(
            email_to='blocked@example.com',
            status='queued',
            scheduled_send_time=fields.Datetime.now() + timedelta(minutes=10),
            sendgrid_batch_id='batch-1',
        )
        suppression = self.env['bhsoft.email.suppression'].create({
            'normalized_email': record.email_to,
            'reason': 'manual',
        })
        self.env['ir.config_parameter'].sudo().set_str(
            'bhs_sendgrid_mailer.api_key', 'test-key',
        )

        with patch.object(type(record), '_cancel_sendgrid_batch') as cancel:
            self.Queue._cron_enforce_email_suppressions()

        cancel.assert_called_once_with('test-key', 'batch-1')
        self.assertEqual(record.status, 'skipped')
        self.assertEqual(record.suppression_id, suppression)
        self.assertFalse(record.cancel_error)

    def test_suppression_enforcement_cancels_unknown_sendgrid_batch(self):
        record = self._queue(
            email_to='unknown-blocked@example.com',
            status='unknown',
            scheduled_send_time=fields.Datetime.now() + timedelta(minutes=10),
            sendgrid_batch_id='batch-unknown',
        )
        self.env['bhsoft.email.suppression'].create({
            'normalized_email': record.email_to,
            'reason': 'manual',
        })
        self.env['ir.config_parameter'].sudo().set_str(
            'bhs_sendgrid_mailer.api_key', 'test-key',
        )

        with patch.object(type(record), '_cancel_sendgrid_batch') as cancel:
            self.Queue._cron_enforce_email_suppressions()

        cancel.assert_called_once_with('test-key', 'batch-unknown')
        self.assertEqual(record.status, 'skipped')

    def test_suppression_enforcement_cancels_smtp_mail(self):
        mail = self.env['mail.mail'].sudo().create({
            'subject': 'Subject',
            'body_html': '<p>Body</p>',
            'email_to': 'smtp-blocked@example.com',
        })
        record = self._queue(
            email_to='smtp-blocked@example.com',
            status='queued',
            scheduled_send_time=fields.Datetime.now() + timedelta(minutes=10),
            mail_id=mail.id,
        )
        suppression = self.env['bhsoft.email.suppression'].create({
            'normalized_email': record.email_to,
            'reason': 'manual',
        })

        self.Queue._cron_enforce_email_suppressions()

        self.assertEqual(mail.state, 'cancel')
        self.assertEqual(record.status, 'skipped')
        self.assertEqual(record.suppression_id, suppression)
        self.assertFalse(record.cancel_error)

    def test_suppression_enforcement_records_cancellation_failure(self):
        record = self._queue(
            email_to='failed-cancel@example.com',
            status='queued',
            scheduled_send_time=fields.Datetime.now() + timedelta(minutes=10),
            sendgrid_batch_id='batch-1',
        )
        self.env['bhsoft.email.suppression'].create({
            'normalized_email': record.email_to,
            'reason': 'manual',
        })
        self.env['ir.config_parameter'].sudo().set_str(
            'bhs_sendgrid_mailer.api_key', '',
        )

        self.Queue._cron_enforce_email_suppressions()

        self.assertEqual(record.status, 'queued')
        self.assertIn('API Key is not configured', record.cancel_error)

    def test_suppression_enforcement_cancels_past_due_unsent_smtp_mail(self):
        mail = self.env['mail.mail'].sudo().create({
            'subject': 'Subject',
            'body_html': '<p>Body</p>',
            'email_to': 'past-due-smtp@example.com',
        })
        record = self._queue(
            email_to='past-due-smtp@example.com',
            status='queued',
            scheduled_send_time=fields.Datetime.now() - timedelta(seconds=1),
            mail_id=mail.id,
        )
        self.env['bhsoft.email.suppression'].create({
            'normalized_email': record.email_to,
            'reason': 'manual',
        })

        self.Queue._cron_enforce_email_suppressions()

        self.assertEqual(mail.state, 'cancel')
        self.assertEqual(record.status, 'skipped')

    def test_suppression_enforcement_ignores_past_due_record(self):
        record = self._queue(
            email_to='past-due@example.com',
            status='queued',
            scheduled_send_time=fields.Datetime.now() - timedelta(seconds=1),
            sendgrid_batch_id='batch-1',
        )
        self.env['bhsoft.email.suppression'].create({
            'normalized_email': record.email_to,
            'reason': 'manual',
        })
        self.env['ir.config_parameter'].sudo().set_str(
            'bhs_sendgrid_mailer.api_key', 'test-key',
        )

        with patch.object(type(record), '_cancel_sendgrid_batch') as cancel:
            self.Queue._cron_enforce_email_suppressions()

        cancel.assert_not_called()
        self.assertEqual(record.status, 'queued')
        self.assertFalse(record.cancel_error)

    def test_cancel_batch_succeeds_with_post(self):
        response = Mock(status_code=201, text='')

        with patch(
            'odoo.addons.bhs_sendgrid_mailer.models.mail_queue.requests.post',
            return_value=response,
        ) as post, patch(
            'odoo.addons.bhs_sendgrid_mailer.models.mail_queue.requests.patch',
        ) as patch_request:
            self.Queue._cancel_sendgrid_batch('test-key', 'batch-1')

        post.assert_called_once()
        patch_request.assert_not_called()

    def test_cancel_existing_batch_status_falls_back_to_patch(self):
        post_response = Mock(
            status_code=400,
            text='status exists',
        )
        post_response.json.return_value = {
            'errors': [{
                'field': 'batch_id',
                'message': 'a status for this batch id exists, try PATCH to update the status',
            }],
        }
        patch_response = Mock(status_code=200, text='')

        with patch(
            'odoo.addons.bhs_sendgrid_mailer.models.mail_queue.requests.post',
            return_value=post_response,
        ), patch(
            'odoo.addons.bhs_sendgrid_mailer.models.mail_queue.requests.patch',
            return_value=patch_response,
        ) as patch_request:
            self.Queue._cancel_sendgrid_batch('test-key', 'batch-1')

        patch_request.assert_called_once_with(
            'https://api.sendgrid.com/v3/user/scheduled_sends/batch-1',
            json={'status': 'cancel'},
            headers={
                'Authorization': 'Bearer test-key',
                'Content-Type': 'application/json',
            },
            timeout=15,
        )

    def test_cancel_unrelated_bad_request_does_not_patch(self):
        response = Mock(status_code=400, text='invalid batch')
        response.json.return_value = {
            'errors': [{'field': 'batch_id', 'message': 'invalid batch id'}],
        }

        with patch(
            'odoo.addons.bhs_sendgrid_mailer.models.mail_queue.requests.post',
            return_value=response,
        ), patch(
            'odoo.addons.bhs_sendgrid_mailer.models.mail_queue.requests.patch',
        ) as patch_request:
            with self.assertRaises(Exception):
                self.Queue._cancel_sendgrid_batch('test-key', 'batch-1')

        patch_request.assert_not_called()

    def test_cancel_selected_records_does_not_cancel_unselected_batch(self):
        scheduled_time = fields.Datetime.now() + timedelta(minutes=10)
        selected = self.Queue.create([
            {
                'name': 'Selected 1',
                'email_to': 'selected1@example.com',
                'subject': 'Subject',
                'body_html': 'Body',
                'status': 'queued',
                'scheduled_send_time': scheduled_time,
                'sendgrid_batch_id': 'batch-selected-1',
            },
            {
                'name': 'Selected 2',
                'email_to': 'selected2@example.com',
                'subject': 'Subject',
                'body_html': 'Body',
                'status': 'queued',
                'scheduled_send_time': scheduled_time,
                'sendgrid_batch_id': 'batch-selected-2',
            },
        ])
        unselected = self._queue(
            email_to='unselected@example.com',
            status='queued',
            scheduled_send_time=scheduled_time,
            sendgrid_batch_id='batch-unselected',
        )
        self.env['ir.config_parameter'].sudo().set_str(
            'bhs_sendgrid_mailer.api_key', 'test-key',
        )

        with patch.object(type(selected), '_cancel_sendgrid_batch') as cancel:
            selected.action_cancel_scheduled()

        self.assertEqual(
            [call.args[-1] for call in cancel.call_args_list],
            ['batch-selected-1', 'batch-selected-2'],
        )
        self.assertEqual(set(selected.mapped('status')), {'cancelled'})
        self.assertEqual(unselected.status, 'queued')

    def test_past_due_record_is_not_cancelled_remotely(self):
        record = self._queue(
            status='queued',
            scheduled_send_time=fields.Datetime.now() - timedelta(seconds=1),
            sendgrid_batch_id='batch-1',
        )

        with patch.object(type(record), '_cancel_sendgrid_batch') as cancel:
            record.action_cancel_scheduled()

        cancel.assert_not_called()
        self.assertEqual(record.status, 'queued')

    def test_older_event_does_not_regress_status(self):
        queue = self._queue(
            status='clicked',
            last_sendgrid_event='click',
            last_sendgrid_event_time='2026-09-23 10:00:00',
        )
        original_terminal_at = queue.terminal_at
        queue._apply_sendgrid_event(
            'delivered',
            {'timestamp': 1_758_621_000},
        )
        self.assertEqual(queue.status, 'clicked')
        self.assertEqual(queue.terminal_at, original_terminal_at)
        self.assertEqual(queue.sendgrid_status, False)
        self.assertEqual(queue.last_sendgrid_event, 'click')

    def test_non_terminal_event_does_not_replace_unsubscribed_status(self):
        queue = self._queue(
            status='unsubscribed',
            sendgrid_status='unsubscribe',
            last_sendgrid_event='unsubscribe',
            last_sendgrid_event_time='2026-09-23 10:00:00',
        )

        queue._apply_sendgrid_event(
            'open',
            {'sg_event_id': 'open-after-unsubscribe', 'timestamp': 1_800_000_000},
        )

        self.assertEqual(queue.status, 'unsubscribed')
        self.assertEqual(queue.sendgrid_status, 'open')
        self.assertEqual(queue.last_sendgrid_event, 'open')

    def test_duplicate_sendgrid_event_is_applied_once(self):
        queue = self._queue(status='queued')
        event = {
            'sg_event_id': 'event-1',
            'event': 'delivered',
            'timestamp': 1_758_621_000,
        }
        queue._apply_sendgrid_event('delivered', event)
        queue._apply_sendgrid_event('delivered', event)
        self.assertEqual(self.env['bhsoft.mail.event'].search_count([
            ('queue_id', '=', queue.id),
        ]), 1)
        self.assertFalse(queue.webhook_log)

    def test_dropped_event_does_not_overwrite_cancelled_status(self):
        queue = self._queue(
            status='cancelled',
            sendgrid_status='cancelled',
            last_sendgrid_event='cancelled',
            cancelled_at=fields.Datetime.now(),
        )
        event = {
            'sg_event_id': 'event-dropped-after-cancel',
            'event': 'dropped',
            'timestamp': 1_758_621_000,
            'reason': 'Scheduled send was cancelled',
        }

        queue._apply_sendgrid_event('dropped', event)

        self.assertEqual(queue.status, 'cancelled')
        self.assertEqual(queue.sendgrid_status, 'dropped')
        self.assertEqual(queue.last_sendgrid_event, 'dropped')
        self.assertFalse(queue.error_message)
        self.assertFalse(queue.webhook_log)
