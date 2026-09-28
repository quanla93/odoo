import json
import random
import time

from odoo import _
from odoo.exceptions import UserError

try:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    service_account = None
    build = None
    HttpError = None


SHEETS_SCOPE = 'https://www.googleapis.com/auth/spreadsheets'


class GoogleSheetsClient:
    def __init__(self, credential_json, retries=3):
        if not service_account or not build:
            raise UserError(_(
                'Google Sheets dependencies are not installed. Install google-auth and '
                'google-api-python-client, then restart Odoo.'
            ))
        try:
            credential_info = json.loads(credential_json or '')
        except (TypeError, ValueError) as error:
            raise UserError(_('The Google Service Account JSON is invalid: %(error)s', error=error)) from error

        if credential_info.get('type') != 'service_account':
            raise UserError(_('The Google credential must be a Service Account JSON document.'))
        if not credential_info.get('client_email') or not credential_info.get('private_key'):
            raise UserError(_('The Service Account JSON is missing client_email or private_key.'))

        try:
            credentials = service_account.Credentials.from_service_account_info(
                credential_info,
                scopes=[SHEETS_SCOPE],
            )
            self.credentials = credentials
            self.auth_request = Request
            self.service = build('sheets', 'v4', credentials=credentials, cache_discovery=False)
        except Exception as error:
            raise UserError(_('Unable to initialize Google Sheets credentials: %(error)s', error=error)) from error

        self.client_email = credential_info['client_email']
        self.project_id = credential_info.get('project_id', '')
        self.retries = max(int(retries), 1)

    def _execute(self, request):
        for attempt in range(self.retries):
            try:
                return request.execute()
            except Exception as error:
                status = getattr(getattr(error, 'resp', None), 'status', None)
                retryable = status in (429, 500, 502, 503, 504) or status is None
                if not retryable or attempt == self.retries - 1:
                    if HttpError and isinstance(error, HttpError):
                        reason = error._get_reason()
                    else:
                        reason = str(error)
                    raise UserError(_('Google Sheets API error: %(error)s', error=reason)) from error
                time.sleep((2 ** attempt) + random.random())

    def validate_credentials(self):
        try:
            self.credentials.refresh(self.auth_request())
        except Exception as error:
            raise UserError(_('Unable to authenticate the Google Service Account: %(error)s', error=error)) from error

    def get_spreadsheet(self, spreadsheet_id):
        request = self.service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields='properties.title,sheets.properties',
        )
        return self._execute(request)

    def read_values(self, spreadsheet_id, range_name):
        request = self.service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueRenderOption='FORMATTED_VALUE',
            dateTimeRenderOption='FORMATTED_STRING',
        )
        response = self._execute(request)
        return response.get('values', [])

    def write_values(self, spreadsheet_id, updates):
        if not updates:
            return
        body = {
            'valueInputOption': 'RAW',
            'data': [
                {'range': range_name, 'values': [[value]]}
                for range_name, value in updates
            ],
        }
        request = self.service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body=body,
        )
        self._execute(request)

    def write_row_ids(self, spreadsheet_id, updates):
        self.write_values(spreadsheet_id, updates)
