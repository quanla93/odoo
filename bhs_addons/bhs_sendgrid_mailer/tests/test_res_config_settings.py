import json
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestResConfigSettings(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env['ir.config_parameter'].sudo()
        cls.google_json = json.dumps({
            'type': 'service_account',
            'client_email': 'mailer@example.iam.gserviceaccount.com',
            'project_id': 'mailer-project',
            'private_key': 'test-private-key',
        })

    def _settings(self, **values):
        defaults = {
            'mail_cron_interval': 30,
            'mail_sync_interval': 15,
            'sendgrid_api_polling_interval': 15,
            'sendgrid_api_polling_window_days': 7,
            'mail_cron_batch_size': 30,
            'mail_cron_delay_min': 45,
            'mail_cron_delay_max': 90,
            'sendgrid_tracking_mode': 'off',
        }
        defaults.update(values)
        return self.env['res.config.settings'].create(defaults)

    def test_retention_defaults(self):
        values = self.env['res.config.settings'].get_values()

        self.assertEqual(values['queue_retention_days'], 90)
        self.assertEqual(values['event_retention_days'], 30)
        self.assertEqual(values['sync_line_retention_days'], 30)
        self.assertEqual(values['sync_run_retention_days'], 365)

    def test_negative_retention_is_rejected(self):
        for field_name in (
            'queue_retention_days',
            'event_retention_days',
            'sync_line_retention_days',
            'sync_run_retention_days',
        ):
            with self.subTest(field_name=field_name), self.assertRaises(ValidationError):
                self._settings(**{field_name: -1}).set_values()

    def test_retention_values_round_trip(self):
        expected = {
            'queue_retention_days': 120,
            'event_retention_days': 45,
            'sync_line_retention_days': 60,
            'sync_run_retention_days': 730,
        }

        self._settings(**expected).set_values()

        values = self.env['res.config.settings'].get_values()
        for field_name, expected_value in expected.items():
            self.assertEqual(values[field_name], expected_value)

    def test_zero_retention_is_allowed(self):
        self._settings(
            queue_retention_days=0,
            event_retention_days=0,
            sync_line_retention_days=0,
            sync_run_retention_days=0,
        ).set_values()

    def test_stored_credentials_are_not_loaded_into_settings(self):
        self.config.set_str('bhs_sendgrid_mailer.api_key', 'secret-api-key')
        self.config.set_str(
            'bhs_sendgrid_mailer.google_service_account_json', self.google_json,
        )
        self.config.set_str(
            'bhs_sendgrid_mailer.webhook_public_key', 'test-public-key',
        )

        values = self.env['res.config.settings'].default_get([
            'sendgrid_api_key',
            'google_service_account_json',
            'sendgrid_webhook_public_key',
        ])

        self.assertFalse(values.get('sendgrid_api_key'))
        self.assertFalse(values.get('google_service_account_json'))
        self.assertFalse(values.get('sendgrid_webhook_public_key'))

    def test_configured_status_exposes_only_safe_metadata(self):
        self.config.set_str('bhs_sendgrid_mailer.api_key', 'secret-api-key')
        self.config.set_str(
            'bhs_sendgrid_mailer.google_service_account_json', self.google_json,
        )
        self.config.set_str(
            'bhs_sendgrid_mailer.webhook_public_key', 'test-public-key',
        )

        settings = self._settings()

        self.assertTrue(settings.sendgrid_api_key_configured)
        self.assertTrue(settings.google_credentials_configured)
        self.assertTrue(settings.sendgrid_webhook_public_key_configured)
        self.assertEqual(
            settings.google_service_account_email,
            'mailer@example.iam.gserviceaccount.com',
        )
        self.assertEqual(settings.google_project_id, 'mailer-project')

    def test_unrelated_save_preserves_stored_credentials(self):
        self.config.set_str('bhs_sendgrid_mailer.api_key', 'secret-api-key')
        self.config.set_str(
            'bhs_sendgrid_mailer.google_service_account_json', self.google_json,
        )
        self.config.set_str(
            'bhs_sendgrid_mailer.webhook_public_key', 'test-public-key',
        )

        self._settings(sendgrid_sender='sender@example.com').set_values()

        self.assertEqual(
            self.config.get_str('bhs_sendgrid_mailer.api_key'), 'secret-api-key',
        )
        self.assertEqual(
            self.config.get_str('bhs_sendgrid_mailer.google_service_account_json'),
            self.google_json,
        )
        self.assertEqual(
            self.config.get_str('bhs_sendgrid_mailer.webhook_public_key'),
            'test-public-key',
        )

    def test_new_credentials_replace_stored_values(self):
        settings = self._settings(
            sendgrid_sender='sender@example.com',
            sendgrid_api_key='new-api-key',
            google_service_account_json=self.google_json,
            sendgrid_webhook_public_key='new-public-key',
        )

        settings.set_values()

        self.assertEqual(
            self.config.get_str('bhs_sendgrid_mailer.api_key'), 'new-api-key',
        )
        self.assertEqual(
            self.config.get_str('bhs_sendgrid_mailer.google_service_account_json'),
            self.google_json,
        )
        self.assertEqual(
            self.config.get_str('bhs_sendgrid_mailer.webhook_public_key'),
            'new-public-key',
        )

    def test_sendgrid_test_uses_stored_key_without_exposing_it(self):
        self.config.set_str('bhs_sendgrid_mailer.api_key', 'secret-api-key')
        settings = self._settings(sendgrid_sender='sender@example.com')

        with patch(
            'odoo.addons.bhs_sendgrid_mailer.models.res_config_settings.requests.get'
        ) as request_get:
            request_get.return_value.status_code = 200
            request_get.return_value.json.return_value = {
                'email': 'sendgrid@example.com',
            }
            settings.action_test_sendgrid_credentials()

        self.assertEqual(
            request_get.call_args.kwargs['headers']['Authorization'],
            'Bearer secret-api-key',
        )

    def test_clear_actions_remove_only_targeted_credentials(self):
        self.config.set_str('bhs_sendgrid_mailer.api_key', 'secret-api-key')
        self.config.set_str(
            'bhs_sendgrid_mailer.google_service_account_json', self.google_json,
        )
        self.config.set_str(
            'bhs_sendgrid_mailer.webhook_public_key', 'test-public-key',
        )
        settings = self._settings()

        settings.action_clear_sendgrid_credentials()
        self.assertFalse(self.config.get_str('bhs_sendgrid_mailer.api_key'))
        self.assertEqual(
            self.config.get_str('bhs_sendgrid_mailer.google_service_account_json'),
            self.google_json,
        )
        settings.action_clear_google_credentials()
        self.assertFalse(
            self.config.get_str('bhs_sendgrid_mailer.google_service_account_json')
        )
        self.assertEqual(
            self.config.get_str('bhs_sendgrid_mailer.webhook_public_key'),
            'test-public-key',
        )
        settings.action_clear_sendgrid_webhook_key()
        self.assertFalse(
            self.config.get_str('bhs_sendgrid_mailer.webhook_public_key')
        )

    def test_existing_webhook_key_satisfies_webhook_validation(self):
        self.config.set_str(
            'bhs_sendgrid_mailer.webhook_public_key', 'test-public-key',
        )

        self._settings(sendgrid_tracking_mode='webhook').set_values()

    def test_clear_webhook_key_requires_webhook_tracking_disabled(self):
        self.config.set_str(
            'bhs_sendgrid_mailer.webhook_public_key', 'test-public-key',
        )
        settings = self._settings(sendgrid_tracking_mode='webhook')

        with self.assertRaises(ValidationError):
            settings.action_clear_sendgrid_webhook_key()

        self.assertEqual(
            self.config.get_str('bhs_sendgrid_mailer.webhook_public_key'),
            'test-public-key',
        )

    def test_google_test_uses_stored_credential(self):
        self.config.set_str(
            'bhs_sendgrid_mailer.google_service_account_json', self.google_json,
        )
        settings = self._settings()

        with patch(
            'odoo.addons.bhs_sendgrid_mailer.models.res_config_settings.GoogleSheetsClient'
        ) as client_class:
            settings.action_test_google_credentials()

        client_class.assert_called_once_with(self.google_json)
        client_class.return_value.validate_credentials.assert_called_once_with()
