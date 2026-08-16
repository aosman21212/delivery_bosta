from datetime import date, timedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class BostaPickupWizard(models.TransientModel):
    _name = 'bosta.pickup.wizard'
    _description = 'Schedule Bosta Pickup'

    picking_ids = fields.Many2many(
        'stock.picking',
        string='Delivery Orders',
        domain=[('is_bosta_carrier', '=', True)],
    )
    pickup_date = fields.Date(
        string='Pickup Date',
        required=True,
        default=lambda self: date.today() + timedelta(days=1),
    )
    pickup_from_time = fields.Char(
        string='From Time',
        default='09:00',
        required=True,
        help='Format: HH:MM (24h)',
    )
    pickup_to_time = fields.Char(
        string='To Time',
        default='18:00',
        required=True,
        help='Format: HH:MM (24h)',
    )
    contact_person = fields.Char(
        string='Contact Person',
        required=True,
    )
    contact_phone = fields.Char(
        string='Contact Phone',
        required=True,
    )
    pickup_address = fields.Char(
        string='Pickup Address',
        required=True,
    )
    notes = fields.Text(string='Notes')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        company = self.env.company
        if 'contact_person' not in res:
            res['contact_person'] = company.name
        if 'contact_phone' not in res:
            res['contact_phone'] = company.phone or ''
        if 'pickup_address' not in res:
            partner = company.partner_id
            res['pickup_address'] = ', '.join(filter(None, [
                partner.street, partner.city,
                partner.state_id.name if partner.state_id else '',
                partner.country_id.name if partner.country_id else '',
            ]))
        return res

    def action_schedule_pickup(self):
        """Send pickup request to Bosta API."""
        self.ensure_one()

        if not self.picking_ids:
            raise UserError(_('Please select at least one delivery order.'))

        # Find carrier from first picking
        carrier = self.picking_ids[0].carrier_id
        if not carrier or carrier.delivery_type != 'bosta':
            raise UserError(_('Selected delivery orders are not using Bosta.'))

        # Build shipment numbers list
        shipment_ids = self.picking_ids.filtered(
            lambda p: p.bosta_shipment_id
        ).mapped('bosta_shipment_id')

        if not shipment_ids:
            raise UserError(_(
                'No Bosta shipments found on the selected delivery orders. '
                'Please validate the delivery orders first to create shipments.'
            ))

        payload = {
            'pickupDate': self.pickup_date.strftime('%Y-%m-%d'),
            'pickupSlot': {
                'start': self.pickup_from_time,
                'end': self.pickup_to_time,
            },
            'contactPerson': {
                'name': self.contact_person,
                'phone': self.contact_phone,
            },
            'pickupAddress': {
                'firstLine': self.pickup_address,
            },
            'shipmentIds': shipment_ids,
            'notes': self.notes or '',
        }

        response = carrier._bosta_api_call('POST', '/pickups', payload)
        data = response.get('data', response)
        pickup_id = data.get('_id') or data.get('id') or ''

        # Store pickup ID on all pickings
        for picking in self.picking_ids:
            if picking.bosta_shipment_id in shipment_ids:
                picking.sudo().write({'bosta_pickup_id': pickup_id})
                picking.message_post(
                    body=_(
                        '📅 Bosta pickup scheduled for %(date)s '
                        '(%(from_t)s – %(to_t)s). Pickup ID: %(pid)s'
                    ) % {
                        'date': self.pickup_date.strftime('%d/%m/%Y'),
                        'from_t': self.pickup_from_time,
                        'to_t': self.pickup_to_time,
                        'pid': pickup_id,
                    },
                )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Pickup Scheduled'),
                'message': _(
                    'Bosta pickup scheduled for %s. Pickup ID: %s'
                ) % (self.pickup_date.strftime('%d/%m/%Y'), pickup_id),
                'type': 'success',
                'sticky': False,
            },
        }
