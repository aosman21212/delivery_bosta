import requests
import base64
import logging
from markupsafe import Markup

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

BOSTA_PROD_URL = 'https://app.bosta.co/api/v2'
BOSTA_STAGING_URL = 'https://stg-app.bosta.co/api/v2'

BOSTA_SHIPMENT_TYPES = [
    ('10', 'SEND — Standard Shipment'),
    ('15', 'CASH_COLLECTION — Collect Payment'),
    ('25', 'CUSTOMER_RETURN — Return from Customer'),
    ('30', 'EXCHANGE — Exchange Product'),
]

BOSTA_PACKAGE_SIZES = [
    ('SMALL',   'Small'),
    ('MEDIUM',  'Medium'),
    ('LARGE',   'Large'),
    ('X_LARGE', 'X-Large'),
]

BOSTA_STATUS_MAP = {
    10:  ('created',      'Created'),
    20:  ('received',     'Received by Bosta'),
    30:  ('in_transit',   'In Transit'),
    40:  ('out_delivery', 'Out for Delivery'),
    45:  ('waiting',      'Waiting for Customer Action'),
    46:  ('scheduled',    'Delivery Scheduled'),
    50:  ('delivered',    'Delivered'),
    60:  ('exception',    'Exception'),
    65:  ('cancel_req',   'Cancellation Requested'),
    70:  ('cancelled',    'Cancelled / Terminated'),
}


class DeliveryCarrier(models.Model):
    _inherit = 'delivery.carrier'

    delivery_type = fields.Selection(
        selection_add=[('bosta', 'Bosta')],
        ondelete={'bosta': 'set default'},
    )

    # ── Credentials ──────────────────────────────────────────────────────
    bosta_api_key = fields.Char(
        string='API Key',
        help='Bosta API Key — found in your Bosta dashboard under Settings → API Keys.',
    )
    bosta_environment = fields.Selection(
        [('prod', 'Production'), ('staging', 'Staging / Test')],
        string='Bosta Environment',
        default='prod',
    )

    # ── Shipment defaults ─────────────────────────────────────────────────
    bosta_default_shipment_type = fields.Selection(
        BOSTA_SHIPMENT_TYPES,
        string='Default Shipment Type',
        default='10',
    )
    bosta_default_package_size = fields.Selection(
        BOSTA_PACKAGE_SIZES,
        string='Default Package Size',
        default='MEDIUM',
    )
    bosta_default_open_package = fields.Boolean(
        string='Allow Open Package by Default',
        default=False,
        help='Allow recipient to inspect package before accepting delivery.',
    )

    # ─────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────

    def _bosta_base_url(self):
        self.ensure_one()
        return BOSTA_PROD_URL if self.bosta_environment == 'prod' else BOSTA_STAGING_URL

    def _bosta_headers(self):
        self.ensure_one()
        if not self.bosta_api_key:
            raise UserError(_(
                'Bosta API Key is not configured on the carrier "%s".'
            ) % self.name)
        return {
            'Authorization': self.sudo().bosta_api_key,
            'Content-Type': 'application/json',
        }

    def _bosta_api_call(self, method, endpoint, payload=None, raw=False):
        """
        Generic Bosta API call.
        :param raw: if True, return raw bytes (for PDF downloads like AWB).
        """
        self.ensure_one()
        url = self._bosta_base_url() + endpoint
        headers = self._bosta_headers()
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                json=payload,
                timeout=30,
            )
        except requests.exceptions.Timeout:
            raise UserError(_(
                'Bosta API request timed out. Please try again.'
            ))
        except requests.exceptions.ConnectionError:
            raise UserError(_(
                'Cannot connect to Bosta API. Check your internet connection.'
            ))

        if raw:
            if response.ok:
                return response.content
            raise UserError(_('Bosta API error (%s): %s') % (response.status_code, response.text[:300]))

        # 204 No Content (success with no body)
        if response.status_code == 204:
            return {}

        try:
            data = response.json()
        except Exception:
            raise UserError(_(
                'Unexpected Bosta API response (HTTP %s): %s'
            ) % (response.status_code, response.text[:300]))

        if not response.ok:
            msg = data.get('message') or data.get('error') or response.text[:300]
            raise UserError(_('Bosta API Error (%s): %s') % (response.status_code, msg))

        return data

    # ─────────────────────────────────────────────────────────────────────
    # Address helper
    # ─────────────────────────────────────────────────────────────────────

    def _bosta_format_address(self, partner):
        """Format an Odoo partner address for Bosta API."""
        city_name = (
            partner.city
            or (partner.state_id.name if partner.state_id else '')
            or ''
        )
        return {
            'firstLine': partner.street or partner.name or '',
            'secondLine': partner.street2 or '',
            'city': {'name': city_name},
            'zone': partner.state_id.name if partner.state_id else city_name,
            'district': partner.street2 or '',
            'country': partner.country_id.code if partner.country_id else 'EG',
        }

    # ─────────────────────────────────────────────────────────────────────
    # Standard Odoo delivery provider methods
    # ─────────────────────────────────────────────────────────────────────

    def bosta_rate_shipment(self, order):
        """Return the shipping price (fixed product price; Bosta has pre-agreed rates)."""
        self.ensure_one()
        if not self.bosta_api_key:
            return {
                'success': False,
                'price': 0.0,
                'error_message': _('Bosta API Key is not configured.'),
                'warning_message': False,
            }
        price = self.product_id.lst_price if self.product_id else 0.0
        return {
            'success': True,
            'price': price,
            'error_message': False,
            'warning_message': _(
                'Bosta shipping cost is the agreed flat rate. '
                'Final cost confirmed on shipment creation.'
            ),
        }

    def bosta_send_shipping(self, pickings):
        """
        Called by Odoo when a delivery order is validated.
        Creates the shipment in Bosta and stores tracking info on the picking.
        Returns list of dicts: [{'tracking_number': ..., 'exact_price': ...}]
        """
        results = []
        for picking in pickings:
            result = self._bosta_create_delivery(picking)
            results.append(result)
        return results

    def _bosta_create_delivery(self, picking):
        """Build payload and send POST /deliveries."""
        partner = picking.partner_id
        if not partner:
            raise UserError(_('Delivery order "%s" has no destination partner.') % picking.name)

        phone = (partner.phone or partner.mobile or '').strip()
        if not phone:
            raise UserError(_(
                'Partner "%s" has no phone number. A phone is required for Bosta shipments.'
            ) % partner.name)

        # ── Package details ───────────────────────────────────────────────
        items_count = max(1, int(sum(
            ml.quantity for ml in picking.move_line_ids
        )))
        description = ', '.join(
            picking.move_ids.mapped('product_id.name')
        )[:200] or 'Shipment'
        weight = picking.bosta_weight or picking.weight or 1.0

        # ── COD ──────────────────────────────────────────────────────────
        cod_amount = 0.0
        if picking.bosta_is_cod:
            cod_amount = picking.bosta_cod_amount
            if not cod_amount and picking.sale_id:
                cod_amount = picking.sale_id.amount_total

        # ── Shipment type ─────────────────────────────────────────────────
        shipment_type = int(
            picking.bosta_shipment_type
            or self.bosta_default_shipment_type
            or '10'
        )

        # ── Package size ──────────────────────────────────────────────────
        package_size = (
            picking.bosta_package_size
            or self.bosta_default_package_size
            or 'MEDIUM'
        )

        # ── Allow open package ────────────────────────────────────────────
        open_package = (
            picking.bosta_open_package
            if picking.bosta_open_package is not None
            else self.bosta_default_open_package
        )

        payload = {
            'type': shipment_type,
            'specs': {
                'packageDetails': {
                    'itemsCount': items_count,
                    'description': description,
                    'weight': weight,
                },
                'size': package_size,
            },
            'cod': cod_amount,
            'dropOffAddress': self._bosta_format_address(partner),
            'receiver': {
                'firstName': (partner.name or '').split()[0][:50],
                'lastName': ' '.join((partner.name or '').split()[1:])[:50] or '-',
                'phone': phone,
                'email': partner.email or '',
            },
            'notes': picking.bosta_notes or picking.note or '',
            'openPackageAllowed': bool(open_package),
            'businessReference': picking.name,
        }

        _logger.debug('Bosta create delivery payload: %s', payload)
        response = self._bosta_api_call('POST', '/deliveries', payload)
        data = response.get('data', response)

        shipment_id = data.get('_id', '') or data.get('id', '')
        tracking_number = (
            data.get('trackingNumber')
            or data.get('tracking_number')
            or data.get('TrackingNumber')
            or ''
        )

        # ── Write results on picking ─────────────────────────────────────
        picking.sudo().write({
            'bosta_shipment_id': shipment_id,
            'bosta_tracking_number': tracking_number,
            'bosta_status': 'created',
            'carrier_tracking_ref': tracking_number,
        })

        # Log note on the picking
        picking.message_post(
            body=Markup(
                _('🚚 <b>Bosta shipment created</b><br/>'
                  'Shipment ID: <code>%s</code><br/>'
                  'Tracking Number: <b>%s</b><br/>'
                  '<a href="%s" target="_blank">Track on Bosta ›</a>')
            ) % (
                shipment_id,
                tracking_number,
                self.bosta_get_tracking_link(picking),
            ),
        )

        _logger.info(
            'Bosta shipment created for %s — ID: %s, Tracking: %s',
            picking.name, shipment_id, tracking_number,
        )
        return {
            'tracking_number': tracking_number,
            'exact_price': 0.0,
        }

    def bosta_get_tracking_link(self, picking):
        """Return the Bosta tracking URL for a picking."""
        tracking = (
            picking.bosta_tracking_number
            or picking.carrier_tracking_ref
            or ''
        )
        if tracking:
            return f'https://bosta.co/tracking-shipment/{tracking}'
        return 'https://bosta.co'

    def bosta_cancel_shipment(self, pickings):
        """Cancel the Bosta shipment when the delivery order is cancelled."""
        for picking in pickings:
            if picking.bosta_shipment_id:
                try:
                    self._bosta_api_call(
                        'DELETE',
                        f'/deliveries/{picking.bosta_shipment_id}',
                    )
                    picking.sudo().write({'bosta_status': 'cancelled'})
                    picking.message_post(
                        body=_('🚫 Bosta shipment cancelled successfully.')
                    )
                except UserError as e:
                    picking.message_post(
                        body=_('⚠ Failed to cancel Bosta shipment: %s') % str(e)
                    )
            if picking.bosta_pickup_id:
                try:
                    self._bosta_api_call(
                        'DELETE',
                        f'/pickups/{picking.bosta_pickup_id}',
                    )
                    picking.message_post(
                        body=_('📅 Bosta pickup appointment cancelled.')
                    )
                except Exception:
                    pass

    # ─────────────────────────────────────────────────────────────────────
    # Additional Bosta-specific actions
    # ─────────────────────────────────────────────────────────────────────

    def action_bosta_test_connection(self):
        """Test API connectivity from the carrier form."""
        self.ensure_one()
        try:
            self._bosta_api_call('GET', '/businesses')
        except UserError as e:
            raise
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Bosta Connection'),
                'message': _('✓ Successfully connected to Bosta API!'),
                'type': 'success',
                'sticky': False,
            },
        }

    def _bosta_update_tracking(self, picking):
        """Fetch current shipment status from Bosta and update the picking."""
        if not picking.bosta_shipment_id:
            raise UserError(_('No Bosta shipment ID on this delivery order.'))

        response = self._bosta_api_call(
            'GET', f'/deliveries/{picking.bosta_shipment_id}'
        )
        data = response.get('data', response)
        state_code = data.get('state', {})
        if isinstance(state_code, dict):
            state_code = state_code.get('value', 0)

        status_tuple = BOSTA_STATUS_MAP.get(int(state_code or 0), ('unknown', str(state_code)))
        picking.sudo().write({'bosta_status': status_tuple[0]})
        return status_tuple[1]

    def _bosta_get_awb(self, picking):
        """Download the Air Waybill PDF from Bosta and attach it to the picking."""
        if not picking.bosta_shipment_id:
            raise UserError(_('No Bosta shipment ID — create the shipment first.'))

        pdf_bytes = self._bosta_api_call(
            'GET',
            f'/deliveries/{picking.bosta_shipment_id}/awb',
            raw=True,
        )

        filename = f'AWB-{picking.bosta_tracking_number or picking.name}.pdf'
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(pdf_bytes),
            'res_model': 'stock.picking',
            'res_id': picking.id,
            'mimetype': 'application/pdf',
        })

        picking.sudo().write({'bosta_awb_attachment_id': attachment.id})
        picking.message_post(
            body=_('📄 AWB label downloaded.'),
            attachment_ids=[attachment.id],
        )
        return attachment
