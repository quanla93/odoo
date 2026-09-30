from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute("""
        UPDATE bhsoft_mail_queue
           SET terminal_at = COALESCE(
               last_sendgrid_event_time,
               actual_sent_time,
               write_date,
               create_date
           )
         WHERE terminal_at IS NULL
           AND status IN (
               'sent', 'delivered', 'opened', 'clicked', 'failed', 'bounced',
               'dropped', 'spamreport', 'unsubscribed', 'cancelled', 'duplicate',
               'skipped'
           )
    """)
    cr.execute("""
        WITH candidates AS (
            SELECT normalized_email,
                   status,
                   COALESCE(
                       last_sendgrid_event_time, write_date, create_date
                   ) AS event_at,
                   error_message,
                   MIN(COALESCE(
                       last_sendgrid_event_time, write_date, create_date
                   )) OVER (PARTITION BY normalized_email) AS first_event_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY normalized_email
                       ORDER BY COALESCE(
                           last_sendgrid_event_time, write_date, create_date
                       ) DESC, id DESC
                   ) AS latest_rank
              FROM bhsoft_mail_queue
             WHERE normalized_email IS NOT NULL
               AND normalized_email != ''
               AND status IN ('bounced', 'spamreport')
        ), latest AS (
            SELECT normalized_email,
                   CASE WHEN status = 'spamreport'
                        THEN 'spam_report'
                        ELSE 'hard_bounce'
                   END AS reason,
                   first_event_at,
                   event_at AS last_event_at,
                   error_message AS last_detail
              FROM candidates
             WHERE latest_rank = 1
        )
        INSERT INTO bhsoft_email_suppression (
            normalized_email, active, reason, first_event_at, last_event_at,
            provider, last_detail,
            create_uid, write_uid, create_date, write_date
        )
        SELECT normalized_email, TRUE, reason, first_event_at, last_event_at,
               'migration', last_detail,
               %s, %s, NOW(), NOW()
          FROM latest
        ON CONFLICT (normalized_email) DO UPDATE SET
            active = TRUE,
            reason = EXCLUDED.reason,
            first_event_at = LEAST(
                COALESCE(
                    bhsoft_email_suppression.first_event_at,
                    EXCLUDED.first_event_at
                ),
                EXCLUDED.first_event_at
            ),
            last_event_at = EXCLUDED.last_event_at,
            provider = EXCLUDED.provider,
            last_detail = EXCLUDED.last_detail,
            write_uid = EXCLUDED.write_uid,
            write_date = NOW()
    """, [SUPERUSER_ID, SUPERUSER_ID])

    suppressions = env['bhsoft.email.suppression'].sudo().search([
        ('active', '=', True),
    ])
    suppressions._enforce_on_pending_queues()
