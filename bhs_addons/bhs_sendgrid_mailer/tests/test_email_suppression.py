from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestEmailSuppression(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Suppression = self.env['bhsoft.email.suppression']

    def test_manual_suppression_normalizes_email(self):
        suppression = self.Suppression.create({
            'normalized_email': ' Test@Example.COM ',
            'reason': 'manual',
        })

        self.assertEqual(suppression.normalized_email, 'test@example.com')
        self.assertTrue(suppression.active)
        self.assertEqual(suppression.provider, 'manual')

    def test_definitive_events_upsert_one_suppression(self):
        first = self.Suppression.upsert_from_sendgrid_event(
            'User@Example.com',
            'bounce',
            {
                'sg_event_id': 'event-1',
                'timestamp': 1_758_621_000,
                'reason': 'Mailbox does not exist',
            },
        )
        second = self.Suppression.upsert_from_sendgrid_event(
            ' user@example.com ',
            'spamreport',
            {
                'sg_event_id': 'event-2',
                'timestamp': 1_758_621_100,
            },
        )

        self.assertEqual(first, second)
        self.assertEqual(second.reason, 'spam_report')
        self.assertEqual(second.last_provider_event_id, 'event-2')
        self.assertEqual(self.Suppression.search_count([
            ('normalized_email', '=', 'user@example.com'),
        ]), 1)

    def test_older_event_does_not_overwrite_latest_reason(self):
        suppression = self.Suppression.upsert_from_sendgrid_event(
            'user@example.com',
            'unsubscribe',
            {'sg_event_id': 'new-event', 'timestamp': 1_758_621_100},
        )
        self.Suppression.upsert_from_sendgrid_event(
            'user@example.com',
            'bounce',
            {'sg_event_id': 'old-event', 'timestamp': 1_758_621_000},
        )

        self.assertEqual(suppression.reason, 'unsubscribe')
        self.assertEqual(suppression.last_provider_event_id, 'new-event')

    def test_non_definitive_events_do_not_suppress(self):
        for event_type in ('deferred', 'dropped', 'delivered'):
            suppression = self.Suppression.upsert_from_sendgrid_event(
                f'{event_type}@example.com', event_type, {'timestamp': 1_758_621_000},
            )
            self.assertFalse(suppression)

    def test_older_event_does_not_reactivate_inactive_suppression(self):
        suppression = self.Suppression.upsert_from_sendgrid_event(
            'user@example.com',
            'unsubscribe',
            {'sg_event_id': 'new-event', 'timestamp': 1_758_621_100},
        )
        suppression.active = False
        deactivated_at = suppression.deactivated_at

        older_timestamp = int(deactivated_at.timestamp()) - 1
        self.assertFalse(suppression.active)
        self.Suppression.upsert_from_sendgrid_event(
            'user@example.com',
            'bounce',
            {'sg_event_id': 'old-event', 'timestamp': older_timestamp},
        )

        suppression.invalidate_recordset(['active', 'reason', 'last_provider_event_id'])
        self.assertFalse(suppression.active)
        self.assertEqual(suppression.reason, 'unsubscribe')
        self.assertEqual(suppression.last_provider_event_id, 'new-event')

    def test_event_after_deactivation_reactivates_suppression(self):
        suppression = self.Suppression.upsert_from_sendgrid_event(
            'reactivate@example.com',
            'bounce',
            {'sg_event_id': 'first-event', 'timestamp': 1_758_621_000},
        )
        suppression.active = False
        newer_timestamp = int(suppression.deactivated_at.timestamp()) + 1

        self.Suppression.upsert_from_sendgrid_event(
            'reactivate@example.com',
            'unsubscribe',
            {'sg_event_id': 'new-event', 'timestamp': newer_timestamp},
        )

        suppression.invalidate_recordset(['active', 'deactivated_at'])
        self.assertTrue(suppression.active)
        self.assertFalse(suppression.deactivated_at)

    def test_referenced_suppression_cannot_be_renamed_or_deleted(self):
        suppression = self.Suppression.create({
            'normalized_email': 'user@example.com',
            'reason': 'manual',
        })
        self.env['bhsoft.mail.queue'].create({
            'name': 'Recipient',
            'email_to': 'user@example.com',
            'subject': 'Subject',
            'body_html': 'Body',
            'status': 'skipped',
            'suppression_id': suppression.id,
        })

        with self.assertRaises(ValidationError):
            suppression.normalized_email = 'renamed@example.com'
        with self.assertRaises(ValidationError):
            suppression.unlink()

        self.assertEqual(suppression.normalized_email, 'user@example.com')

    def test_new_event_reactivates_inactive_provider_suppression(self):
        suppression = self.Suppression.upsert_from_sendgrid_event(
            'user@example.com',
            'bounce',
            {'sg_event_id': 'bounce-1', 'timestamp': 1_758_621_000},
        )
        suppression.active = False
        newer_timestamp = int(suppression.deactivated_at.timestamp()) + 1

        updated = self.Suppression.upsert_from_sendgrid_event(
            'user@example.com',
            'group_unsubscribe',
            {'sg_event_id': 'unsubscribe-1', 'timestamp': newer_timestamp},
        )

        self.assertEqual(updated, suppression)
        self.assertTrue(updated.active)
        self.assertEqual(updated.reason, 'unsubscribe')

