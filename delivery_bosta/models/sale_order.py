from odoo import models, fields, api


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # Convenience computed field so the user can see Bosta shipment summary
    # directly on the sale order.
    bosta_tracking_info = fields.Char(
        string='Bosta Tracking',
        compute='_compute_bosta_tracking_info',
        store=False,
    )
    is_bosta_order = fields.Boolean(
        compute='_compute_is_bosta_order',
        store=False,
    )

    @api.depends('carrier_id', 'carrier_id.delivery_type')
    def _compute_is_bosta_order(self):
        for order in self:
            order.is_bosta_order = (
                order.carrier_id
                and order.carrier_id.delivery_type == 'bosta'
            )

    @api.depends('picking_ids.bosta_tracking_number', 'picking_ids.bosta_status')
    def _compute_bosta_tracking_info(self):
        for order in self:
            pickings = order.picking_ids.filtered(
                lambda p: p.bosta_tracking_number
            )
            if pickings:
                order.bosta_tracking_info = ', '.join(
                    p.bosta_tracking_number for p in pickings
                )
            else:
                order.bosta_tracking_info = ''
