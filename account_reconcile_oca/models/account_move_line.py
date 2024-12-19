# Copyright 2023 Dixmit
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json

from odoo import _, models
from odoo.exceptions import ValidationError


class AccountMoveLine(models.Model):

    _inherit = "account.move.line"

    def action_reconcile_manually(self):
        if not self:
            return {}
        accounts = self.mapped("account_id")
        if len(accounts) > 1:
            raise ValidationError(
                _("You can only reconcile journal items belonging to the same account.")
            )
        partner = self.mapped("partner_id")
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "account_reconcile_oca.account_account_reconcile_act_window"
        )
        action["domain"] = [("account_id", "=", self.mapped("account_id").id)]
        if len(partner) == 1 and self.account_id.account_type in [
            "asset_receivable",
            "liability_payable",
        ]:
            action["domain"] += [("partner_id", "=", partner.id)]
        action["context"] = self.env.context.copy()
        action["context"]["default_account_move_lines"] = self.filtered(
            lambda r: not r.reconciled
        ).ids
        return action

    def _prepare_exchange_difference_move_vals(
        self, amounts_list, company=None, exchange_date=None, **kwargs
    ):
        # This updates the analytic_distribution of the exchange lines,
        # otherwise the move lines originated from this function
        # Will never have analytic
        move_vals = super()._prepare_exchange_difference_move_vals(
            amounts_list, company=company, exchange_date=exchange_date, **kwargs
        )

        exchange_analytic = {}

        for move, _sequence in move_vals["to_reconcile"]:
            # Looks for the data of account.bank.statement.line
            # That has the analytic_distribution of the exchange move_line
            self.env.cr.execute(
                r"""
                SELECT reconcile_data
                FROM account_bank_statement_line
                WHERE EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(reconcile_data::jsonb->'data') AS elem
                    WHERE (elem->>'id') ~ '^\d+$'  -- Verify that it's a number
                    AND (elem->>'id')::int = %s
                );
                """,
                (move.id,),
            )
            reconcile_data = self.env.cr.fetchall()
            if reconcile_data:
                parsed_data = json.loads(reconcile_data[0][0])
                data_items = parsed_data.get("data", [])

                # Checks the exchange move of the reconcile_data
                result = next(
                    (
                        item
                        for item in data_items
                        if item.get("is_exchange_counterpart")
                        and item.get("original_exchange_line_id") == move.id
                    ),
                    None,
                )

                if result:
                    # Maps the financial account and amount with his analytic
                    key = f"{result['account_id'][0]}|{abs(result['net_amount'])}"
                    exchange_analytic[key] = result["analytic_distribution"]

        for line in move_vals["move_vals"]["line_ids"]:
            line_data = line[2]
            account_id = line_data["account_id"]
            credit = line_data["credit"]
            debit = line_data["debit"]

            key = f"{account_id}|{credit if credit > 0 else debit}"

            analytic_distribution = exchange_analytic.get(key, None)

            if analytic_distribution:
                line_data.update({"analytic_distribution": analytic_distribution})

        return move_vals
