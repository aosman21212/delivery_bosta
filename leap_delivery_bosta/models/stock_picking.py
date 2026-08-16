from odoo import models, fields, api, _
from odoo.exceptions import UserError


BOSTA_STATUS_SELECTION = [
    ('created',      'Created'),
    ('received',     'Received by Bosta'),
    ('in_transit',   'In Transit'),
    ('out_delivery', 'Out for Delivery'),
    ('waiting',      'Waiting for Customer Action'),
    ('scheduled',    'Delivery Scheduled'),
    ('delivered',    'Delivered'),
    ('exception',    'Exception'),
    ('cancel_req',   'Cancellation Requested'),
    ('cancelled',    'Cancelled'),
    ('unknown',      'Unknown'),
]

BOSTA_STATUS_COLORS = {
    'created':      'info',
    'received':     'info',
    'in_transit':   'warning',
    'out_delivery': 'warning',
    'waiting':      'warning',
    'scheduled':    'info',
    'delivered':    'success',
    'exception':    'danger',
    'cancel_req':   'danger',
    'cancelled':    'danger',
    'unknown':      'secondary',
}


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    # ── Shipment options ──────────────────────────────────────────────────
    bosta_shipment_type = fields.Selection(
        [
            ('10', 'SEND — Standard Shipment'),
            ('15', 'CASH_COLLECTION — Collect Payment'),
            ('25', 'CUSTOMER_RETURN — Return from Customer'),
            ('30', 'EXCHANGE — Exchange Product'),
        ],
        string='Shipment Type',
        default='10',
    )
    bosta_package_size = fields.Selection(
        [
            ('SMALL',   'Small'),
            ('MEDIUM',  'Medium'),
            ('LARGE',   'Large'),
            ('X_LARGE', 'X-Large'),
        ],
        string='Package Size',
        default='MEDIUM',
    )
    bosta_weight = fields.Float(
        string='Weight (kg)',
        default=1.0,
        digits=(10, 3),
    )
    bosta_is_cod = fields.Boolean(
        string='Cash on Delivery (COD)',
        default=False,
    )
    bosta_cod_amount = fields.Float(
        string='COD Amount',
        digits=(10, 2),
        help='Amount to collect from the recipient. Leave 0 to use the sale order total.',
    )
    bosta_open_package = fields.Boolean(
        string='Allow Open Before Delivery',
        default=False,
        help='Allow recipient to inspect the package before accepting.',
    )
    bosta_notes = fields.Text(
        string='Delivery Notes',
        help='Notes for the Bosta courier (e.g. call before delivery).',
    )

    # ── Bosta response fields ─────────────────────────────────────────────
    bosta_shipment_id = fields.Char(
        string='Bosta Shipment ID',
        copy=False,
        readonly=True,
        help='Internal Bosta shipment _id.',
    )
    bosta_tracking_number = fields.Char(
        string='Bosta Tracking Number',
        copy=False,
        readonly=True,
    )
    bosta_tracking_url = fields.Char(
        string='Bosta Tracking URL',
        compute='_compute_bosta_tracking_url',
        store=False,
    )
    bosta_status = fields.Selection(
        BOSTA_STATUS_SELECTION,
        string='Bosta Status',
        copy=False,
        readonly=True,
        tracking=True,
    )
    bosta_status_label = fields.Char(
        string='Status Label',
        compute='_compute_bosta_status_label',
        store=False,
    )
    bosta_awb_attachment_id = fields.Many2one(
        'ir.attachment',
        string='AWB Label',
        copy=False,
        readonly=True,
    )
    bosta_pickup_id = fields.Char(
        string='Bosta Pickup ID',
        copy=False,
        readonly=True,
    )

    # ── Visibility helper ─────────────────────────────────────────────────
    is_bosta_carrier = fields.Boolean(
        compute='_compute_is_bosta_carrier',
        store=False,
    )

    # ─────────────────────────────────────────────────────────────────────
    # Computed fields
    # ─────────────────────────────────────────────────────────────────────

    @api.depends('carrier_id', 'carrier_id.delivery_type')
    def _compute_is_bosta_carrier(self):
        for picking in self:
            picking.is_bosta_carrier = (
                picking.carrier_id
                and picking.carrier_id.delivery_type == 'bosta'
            )

    @api.depends('bosta_tracking_number', 'carrier_tracking_ref')
    def _compute_bosta_tracking_url(self):
        for picking in self:
            tracking = picking.bosta_tracking_number or picking.carrier_tracking_ref
            if tracking:
                picking.bosta_tracking_url = (
                    f'https://bosta.co/tracking-shipment/{tracking}'
                )
            else:
                picking.bosta_tracking_url = ''

    @api.depends('bosta_status')
    def _compute_bosta_status_label(self):
        label_map = dict(BOSTA_STATUS_SELECTION)
        for picking in self:
            picking.bosta_status_label = label_map.get(
                picking.bosta_status, ''
            )

    # ─────────────────────────────────────────────────────────────────────
    # Default from carrier
    # ─────────────────────────────────────────────────────────────────────

    @api.onchange('carrier_id')
    def _onchange_bosta_carrier(self):
        if self.carrier_id and self.carrier_id.delivery_type == 'bosta':
            self.bosta_shipment_type = (
                self.carrier_id.bosta_default_shipment_type or '10'
            )
            self.bosta_package_size = (
                self.carrier_id.bosta_default_package_size or 'MEDIUM'
            )
            self.bosta_open_package = (
                self.carrier_id.bosta_default_open_package or False
            )

    # ─────────────────────────────────────────────────────────────────────
    # Action buttons
    # ─────────────────────────────────────────────────────────────────────

    def action_bosta_update_status(self):
        """Fetch the latest shipment status from Bosta."""
        self.ensure_one()
        if not self.is_bosta_carrier:
            raise UserError(_('This delivery is not using Bosta.'))
        status_label = self.carrier_id._bosta_update_tracking(self)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Bosta Status Updated'),
                'message': _('Current status: %s') % status_label,
                'type': 'success',
                'sticky': False,
            },
        }

    def action_bosta_get_awb(self):
        """Download and attach the Air Waybill PDF from Bosta."""
        self.ensure_one()
        if not self.is_bosta_carrier:
            raise UserError(_('This delivery is not using Bosta.'))
        attachment = self.carrier_id._bosta_get_awb(self)
        # Open the PDF
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'new',
        }

    def action_bosta_print_awb(self):
        """Download AWB (if not already done) and return for printing."""
        self.ensure_one()
        if not self.bosta_awb_attachment_id:
            self.action_bosta_get_awb()
        if self.bosta_awb_attachment_id:
            return {
                'type': 'ir.actions.act_url',
                'url': f'/web/content/{self.bosta_awb_attachment_id.id}?download=true',
                'target': 'new',
            }

    def action_bosta_schedule_pickup(self):
        """Open the pickup scheduling wizard."""
        self.ensure_one()
        if not self.is_bosta_carrier:
            raise UserError(_('This delivery is not using Bosta.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Schedule Bosta Pickup'),
            'res_model': 'bosta.pickup.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_picking_ids': [self.id],
                'default_contact_person': self.company_id.name,
                'default_contact_phone': self.company_id.phone or '',
            },
        }

    def action_bosta_open_tracking(self):
        """Open tracking page in a new browser tab."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': self.bosta_tracking_url or 'https://bosta.co',
            'target': 'new',
        }
