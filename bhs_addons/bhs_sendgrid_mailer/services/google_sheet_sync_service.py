import hashlib
import re
import uuid

from odoo import _, fields

from .google_sheets_client import GoogleSheetsClient


REQUIRED_HEADERS = ('email', 'subject')
DEFAULT_HEADERS = {
    'row_id': 'odoo row id',
    'name': 'contact',
    'email': 'email',
    'subject': 'subject',
    'body': 'body',
    'hubspot_id': 'hubspot id',
    'linkedin_url': 'linkedin',
    'company': 'company',
    'company_website': 'company website',
}
LOCK_NAMESPACE = 4_273_051


def normalize_header(value):
    return re.sub(r'\s+', ' ', str(value or '').strip().lower())


def column_letter(index):
    result = ''
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def canonical_text(value):
    return re.sub(r'\s+', ' ', str(value or '').strip())


def digest(parts):
    value = '\x1f'.join(parts).encode('utf-8')
    return hashlib.sha256(value).hexdigest()


class GoogleSheetSyncService:
    def __init__(self, source):
        self.source = source
        self.env = source.env
        self.Queue = self.env['bhsoft.mail.queue'].sudo()
        self.Run = self.env['bhsoft.google.sheet.sync.run'].sudo()
        self.Line = self.env['bhsoft.google.sheet.sync.line'].sudo()
        self.client = None

    def sync(self):
        self.source.ensure_one()
        run = self.Run.create({'source_id': self.source.id})
        if not self._acquire_lock():
            run.write({
                'state': 'failed',
                'finished_at': fields.Datetime.now(),
                'error_message': _('This source is already being synchronized.'),
            })
            return run

        counters = {
            'total_count': 0,
            'created_count': 0,
            'updated_count': 0,
            'unchanged_count': 0,
            'duplicate_count': 0,
            'skipped_count': 0,
            'conflict_count': 0,
            'error_count': 0,
        }
        now = fields.Datetime.now()
        try:
            self.client = GoogleSheetsClient(self.source._get_google_credential_json())
            values = self.client.read_values(self.source.spreadsheet_id, self.source._sheet_range())
            if not values:
                raise ValueError(_('The configured sheet is empty.'))
            headers = values[0]
            original_header_count = len(headers)
            row_id_added = self._ensure_row_id_header(headers)
            header_map = self._header_map(headers)
            rows = list(self._prepare_rows(
                values[1:], header_map, original_header_count if row_id_added else None,
            ))
            counters['total_count'] = len(rows)
            self._validate_row_ids(rows, run, counters)
            self._write_missing_row_ids(rows, header_map)
            self._process_rows(rows, run, counters, now)
            state = 'partial' if counters['error_count'] or counters['conflict_count'] else 'success'
            run.write({
                **counters,
                'state': state,
                'finished_at': fields.Datetime.now(),
            })
            self.source.write({
                'last_sync_at': now,
                'last_success_at': fields.Datetime.now(),
                'last_error': False,
                'failure_count': 0,
            })
            return run
        except Exception as error:
            run.write({
                **counters,
                'state': 'failed',
                'finished_at': fields.Datetime.now(),
                'error_message': str(error),
            })
            self.source.write({
                'last_sync_at': now,
                'last_error': str(error),
                'failure_count': self.source.failure_count + 1,
            })
            return run

    def _acquire_lock(self):
        self.env.cr.execute(
            'SELECT pg_try_advisory_xact_lock(%s, %s)',
            (LOCK_NAMESPACE, self.source.id),
        )
        return bool(self.env.cr.fetchone()[0])

    def _ensure_row_id_header(self, headers):
        configured = self.source._column_headers()
        normalized = [normalize_header(header) for header in headers]
        row_id_header = configured['row_id']
        if normalize_header(row_id_header) in normalized:
            return False

        start_column, end_column = self.source.range_columns.strip().upper().split(':', 1)
        start_index = self._column_index(start_column)
        end_index = self._column_index(end_column)
        row_id_index = max(len(headers), start_index)
        if row_id_index > end_index:
            raise ValueError(_(
                'The configured range has no empty column for "%(header)s". Extend Column Range or add the column manually.',
                header=row_id_header,
            ))

        headers.extend([''] * (row_id_index - len(headers)))
        headers.append(row_id_header)
        cell = self._cell_range(row_id_index, self.source.header_row)
        self.client.write_values(self.source.spreadsheet_id, [(cell, row_id_header)])
        return True

    @staticmethod
    def _column_index(letters):
        result = 0
        for letter in letters:
            result = result * 26 + ord(letter) - 64
        return result - 1

    def _cell_range(self, column_index, row_number):
        escaped_name = self.source.sheet_name.replace("'", "''")
        return f"'{escaped_name}'!{column_letter(column_index)}{row_number}"

    def _header_map(self, headers):
        normalized = [normalize_header(header) for header in headers]
        duplicates = {header for header in normalized if header and normalized.count(header) > 1}
        if duplicates:
            raise ValueError(_('Duplicate Google Sheets headers: %(headers)s', headers=', '.join(sorted(duplicates))))

        configured = self.source._column_headers()
        result = {}
        for key, expected in configured.items():
            normalized_expected = normalize_header(expected)
            if normalized_expected in normalized:
                result[key] = normalized.index(normalized_expected)

        missing = [configured[key] for key in REQUIRED_HEADERS if key not in result]
        if missing:
            raise ValueError(_('Missing required Google Sheets columns: %(headers)s', headers=', '.join(missing)))
        if 'row_id' not in result:
            raise ValueError(_(
                'Missing the "%(header)s" column. Add it before enabling synchronization.',
                header=configured['row_id'],
            ))
        return result

    def _prepare_rows(self, raw_rows, header_map, row_id_insert_index=None):
        first_data_row = self.source.header_row + 1
        for offset, raw_row in enumerate(raw_rows):
            row = list(raw_row)
            if row_id_insert_index is not None:
                row.insert(row_id_insert_index, '')
            row_number = first_data_row + offset

            def raw_get(key):
                index = header_map.get(key)
                if index is None or index >= len(row):
                    return ''
                return str(row[index] or '')

            def get(key):
                return canonical_text(raw_get(key))

            business_keys = (
                'name', 'email', 'subject', 'body', 'hubspot_id',
                'linkedin_url', 'company', 'company_website',
            )
            if not any(get(key) for key in business_keys):
                continue

            email = self.Queue._normalize_email(get('email'))
            subject = get('subject')
            body = raw_get('body')
            source_values = {
                'name': get('name') or _('Customer'),
                'email_to': get('email'),
                'company_name': get('company') or False,
                'subject': subject,
                'body_html': body,
                'hubspot_id': get('hubspot_id') or False,
                'linkedin_url': get('linkedin_url') or False,
                'company_website': get('company_website') or False,
            }
            source_hash = digest([
                canonical_text(source_values['name']), email, subject, body,
                canonical_text(source_values['hubspot_id']), canonical_text(source_values['linkedin_url']),
                canonical_text(source_values['company_name']), canonical_text(source_values['company_website']),
            ])
            content_hash = digest([email, subject, body])
            yield {
                'row_number': row_number,
                'row_id': get('row_id'),
                'email': email,
                'values': source_values,
                'source_hash': source_hash,
                'content_hash': content_hash,
                'valid': True,
            }

    def _validate_row_ids(self, rows, run, counters):
        grouped = {}
        for row in rows:
            if row['row_id']:
                grouped.setdefault(row['row_id'], []).append(row)
        for row_id, duplicate_rows in grouped.items():
            if len(duplicate_rows) < 2:
                continue
            for row in duplicate_rows:
                row['valid'] = False
                self._line(run, row, 'error', _(
                    'The Odoo Row ID is duplicated in rows %(rows)s.',
                    rows=', '.join(str(item['row_number']) for item in duplicate_rows),
                ))
                counters['error_count'] += 1

    def _write_missing_row_ids(self, rows, header_map):
        updates = []
        for row in rows:
            if row['valid'] and not row['row_id']:
                row['row_id'] = str(uuid.uuid4())
                cell = self._cell_range(header_map['row_id'], row['row_number'])
                updates.append((cell, row['row_id']))
        if updates:
            self.client.write_row_ids(self.source.spreadsheet_id, updates)

    def _process_rows(self, rows, run, counters, synced_at):
        seen_row_ids = {row['row_id'] for row in rows if row['row_id']}
        seen_content = {}
        suppressions = self.env['bhsoft.email.suppression'].sudo().active_by_email(
            [row['email'] for row in rows if row['valid']]
        )
        for row in rows:
            if not row['valid']:
                continue
            try:
                with self.env.cr.savepoint():
                    dedupe_key = (row['email'], row['content_hash'])
                    duplicate_queue = seen_content.get(dedupe_key)
                    suppression = suppressions.get(row['email'])
                    result, queue, message = self._upsert_row(
                        row,
                        run,
                        synced_at,
                        duplicate_queue=duplicate_queue,
                        suppression=suppression,
                    )
                    if result not in ('duplicate', 'error') and (
                        result != 'skipped' or suppression
                    ):
                        seen_content.setdefault(dedupe_key, queue)
                    counters[f'{result}_count'] += 1
                    self._line(run, row, result, message, queue)
            except Exception as error:
                counters['error_count'] += 1
                self._line(run, row, 'error', str(error))

        existing = self.Queue.search([
            ('sheet_source_id', '=', self.source.id),
            ('sheet_row_id', '!=', False),
        ])
        missing = existing.filtered(lambda queue: queue.sheet_row_id not in seen_row_ids)
        missing.write({'source_missing': True})
        (existing - missing).write({'source_missing': False})

    def _upsert_row(
        self, row, run, synced_at, duplicate_queue=False, suppression=False,
    ):
        values = row['values']
        if not values['email_to']:
            raise ValueError(_('Email is required.'))
        if not self.Queue._is_valid_email(values['email_to']):
            raise ValueError(_('Invalid email format: %(email)s', email=values['email_to']))
        if not values['subject']:
            raise ValueError(_('Subject is required.'))
        if not (values['body_html'] or '').strip():
            raise ValueError(_('Body is required.'))

        queue = self.Queue.search([
            ('sheet_source_id', '=', self.source.id),
            ('sheet_row_id', '=', row['row_id']),
        ], limit=1)
        duplicate = duplicate_queue or self.Queue.search([
            ('sheet_source_id', '=', self.source.id),
            ('normalized_email', '=', row['email']),
            ('content_hash', '=', row['content_hash']),
            ('sheet_row_id', '!=', row['row_id']),
            ('status', 'not in', ('duplicate', 'skipped')),
        ], order='id asc', limit=1)
        suppression_values = {
            'status': 'skipped',
            'suppression_id': suppression.id,
            'duplicate_of_id': False,
            'error_message': _(
                'Skipped because this recipient is suppressed: %(reason)s',
                reason=suppression.reason,
            ),
        } if suppression else {}
        common = {
            **values,
            'sheet_source_id': self.source.id,
            'sheet_row_id': row['row_id'],
            'sheet_row_number': row['row_number'],
            'source_hash': row['source_hash'],
            'content_hash': row['content_hash'],
            'source_synced_at': synced_at,
            'sync_run_id': run.id,
            'source_missing': False,
        }

        if queue:
            if suppression and queue.status in ('none', 'skipped'):
                if queue.status == 'skipped' and queue.suppression_id:
                    sync_values = {
                        'sheet_row_number': row['row_number'],
                        'source_synced_at': synced_at,
                        'sync_run_id': run.id,
                        'source_missing': False,
                    }
                    if not queue.compacted_at:
                        sync_values.update(common)
                    queue.write({
                        **sync_values,
                        **suppression_values,
                        'terminal_at': queue.terminal_at or fields.Datetime.now(),
                    })
                else:
                    queue.write({
                        **common,
                        **suppression_values,
                        'terminal_at': queue.terminal_at or fields.Datetime.now(),
                    })
                return 'skipped', queue, suppression_values['error_message']
            if (
                not suppression
                and queue.suppression_id
                and queue.status == 'skipped'
                and (
                    not queue.suppression_id.active
                    or queue.normalized_email != row['email']
                )
            ):
                common.update({
                    'status': 'none',
                    'suppression_id': False,
                    'terminal_at': False,
                    'compacted_at': False,
                    'error_message': False,
                })
                queue.write(common)
                return 'updated', queue, False
            if queue.source_hash == row['source_hash']:
                queue.write({
                    'sheet_row_number': row['row_number'],
                    'source_synced_at': synced_at,
                    'sync_run_id': run.id,
                    'source_missing': False,
                })
                return 'unchanged', queue, False
            if queue.status != 'none':
                queue.write({
                    'source_conflict': True,
                    'source_conflict_message': _(
                        'The Sheet row changed after the message left Pending. The submitted content was preserved.'
                    ),
                    'source_synced_at': synced_at,
                    'sync_run_id': run.id,
                })
                return 'conflict', queue, queue.source_conflict_message
            common.update({
                'source_conflict': False,
                'source_conflict_message': False,
                'duplicate_of_id': duplicate.id if duplicate else False,
                'status': 'duplicate' if duplicate else 'none',
                'error_message': _(
                    'Skipped because the same email and content already exist in this Google Sheet source.'
                ) if duplicate else False,
            })
            queue.write(common)
            return ('duplicate' if duplicate else 'updated'), queue, False

        if suppression:
            common.update(suppression_values)
            queue = self.Queue.create(common)
            return 'skipped', queue, suppression_values['error_message']
        common.update({
            'status': 'duplicate' if duplicate else 'none',
            'duplicate_of_id': duplicate.id if duplicate else False,
            'error_message': _(
                'Skipped because the same email and content already exist in this Google Sheet source.'
            ) if duplicate else False,
        })
        queue = self.Queue.create(common)
        return ('duplicate' if duplicate else 'created'), queue, False

    def _line(self, run, row, result, message=False, queue=False):
        self.Line.create({
            'run_id': run.id,
            'row_number': row['row_number'],
            'row_id': row.get('row_id') or False,
            'email': row.get('email') or False,
            'result': result,
            'message': message or False,
            'queue_id': queue.id if queue else False,
        })
