"""Energy (H10, R2): sources, prices, forecasts, the energy plan, charging and savings.

HouseMind takes the energy setup from Home Assistant's energy settings (E-65) and plans per
quarter of an hour with explainable rules (B-34). Every step of the plan is an action request
through the safety gate (H4).
"""
