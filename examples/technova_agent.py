"""TechNova Customer Support Agent — wired with recursive-improve trace capture.

Based on Anthropic's courses/tool_use example. Uses the anthropic SDK directly.

Usage:
    # Install deps (no litellm needed):
    pip install anthropic recursive-improve

    # Set your API key:
    export ANTHROPIC_API_KEY=sk-ant-...

    # Run test scenarios (automated, no interactive input):
    python examples/technova_agent.py

    # Run interactive chat mode:
    python examples/technova_agent.py --interactive

    Traces are saved to ./eval/traces/ for analysis with recursive-improve.
"""

import json
import re
import sys

import anthropic
import recursive_improve as ri

# --- Config ---
MODEL_NAME = "claude-sonnet-4-20250514"

SYSTEM_PROMPT = """\
You are a customer support chat bot for an online retailer called TechNova.
Your job is to help users look up their account, orders, and cancel orders.
Be helpful and brief in your responses.
When confirming actions to the user, only state information that was returned by tool responses. Do not fabricate refund timelines, return policies, or other operational details.
Always verify the customer's identity using get_user before looking up or modifying their orders. Do not use get_order_by_id or cancel_order without first confirming the customer's account.
Only suggest actions you can perform with your available tools. Do not offer returns, exchanges, escalations, or other processes that are not supported by your tool set.
You have access to a set of tools, but only use them when needed.
If you do not have enough information to use a tool correctly, ask a user follow up questions to get the required inputs.
Do not call any of the tools unless you have the required data from a user.
"""

# --- Fake Database ---

class FakeDatabase:
    def __init__(self):
        self.customers = [
            {"id": "1213210", "name": "John Doe", "email": "john@gmail.com", "phone": "123-456-7890", "username": "johndoe"},
            {"id": "2837622", "name": "Priya Patel", "email": "priya@candy.com", "phone": "987-654-3210", "username": "priya123"},
            {"id": "3924156", "name": "Liam Nguyen", "email": "lnguyen@yahoo.com", "phone": "555-123-4567", "username": "liamn"},
            {"id": "4782901", "name": "Aaliyah Davis", "email": "aaliyahd@hotmail.com", "phone": "111-222-3333", "username": "adavis"},
            {"id": "5190753", "name": "Hiroshi Nakamura", "email": "hiroshi@gmail.com", "phone": "444-555-6666", "username": "hiroshin"},
            {"id": "6824095", "name": "Fatima Ahmed", "email": "fatimaa@outlook.com", "phone": "777-888-9999", "username": "fatimaahmed"},
            {"id": "7135680", "name": "Alejandro Rodriguez", "email": "arodriguez@protonmail.com", "phone": "222-333-4444", "username": "alexr"},
            {"id": "8259147", "name": "Megan Anderson", "email": "megana@gmail.com", "phone": "666-777-8888", "username": "manderson"},
            {"id": "9603481", "name": "Kwame Osei", "email": "kwameo@yahoo.com", "phone": "999-000-1111", "username": "kwameo"},
            {"id": "1057426", "name": "Mei Lin", "email": "meilin@gmail.com", "phone": "333-444-5555", "username": "mlin"},
        ]
        self.orders = [
            {"id": "24601", "customer_id": "1213210", "product": "Wireless Headphones", "quantity": 1, "price": 79.99, "status": "Shipped"},
            {"id": "13579", "customer_id": "1213210", "product": "Smartphone Case", "quantity": 2, "price": 19.99, "status": "Processing"},
            {"id": "97531", "customer_id": "2837622", "product": "Bluetooth Speaker", "quantity": 1, "price": 49.99, "status": "Shipped"},
            {"id": "86420", "customer_id": "3924156", "product": "Fitness Tracker", "quantity": 1, "price": 129.99, "status": "Delivered"},
            {"id": "54321", "customer_id": "4782901", "product": "Laptop Sleeve", "quantity": 3, "price": 24.99, "status": "Shipped"},
            {"id": "19283", "customer_id": "5190753", "product": "Wireless Mouse", "quantity": 1, "price": 34.99, "status": "Processing"},
            {"id": "74651", "customer_id": "6824095", "product": "Gaming Keyboard", "quantity": 1, "price": 89.99, "status": "Delivered"},
            {"id": "30298", "customer_id": "7135680", "product": "Portable Charger", "quantity": 2, "price": 29.99, "status": "Shipped"},
            {"id": "47652", "customer_id": "8259147", "product": "Smartwatch", "quantity": 1, "price": 199.99, "status": "Processing"},
            {"id": "61984", "customer_id": "9603481", "product": "Noise-Cancelling Headphones", "quantity": 1, "price": 149.99, "status": "Shipped"},
            {"id": "58243", "customer_id": "1057426", "product": "Wireless Earbuds", "quantity": 2, "price": 99.99, "status": "Delivered"},
            {"id": "90357", "customer_id": "1213210", "product": "Smartphone Case", "quantity": 1, "price": 19.99, "status": "Shipped"},
            {"id": "28164", "customer_id": "2837622", "product": "Wireless Headphones", "quantity": 2, "price": 79.99, "status": "Processing"},
        ]

    def get_user(self, key, value):
        if key in {"email", "phone", "username"}:
            for customer in self.customers:
                if customer[key] == value:
                    return customer
            return f"Couldn't find a user with {key} of {value}"
        else:
            raise ValueError(f"Invalid key: {key}")

    def get_order_by_id(self, order_id):
        for order in self.orders:
            if order["id"] == order_id:
                return order
        return None

    def get_customer_orders(self, customer_id):
        return [order for order in self.orders if order["customer_id"] == customer_id]

    def cancel_order(self, order_id):
        order = self.get_order_by_id(order_id)
        if order:
            if order["status"] == "Processing":
                order["status"] = "Cancelled"
                return "Cancelled the order"
            else:
                return "Order has already shipped. Can't cancel it."
        return "Can't find that order!"


# --- Tool Definitions ---

TOOLS = [
    {
        "name": "get_user",
        "description": "Looks up a user by email, phone, or username.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "enum": ["email", "phone", "username"],
                    "description": "The attribute to search for a user by (email, phone, or username).",
                },
                "value": {
                    "type": "string",
                    "description": "The value to match for the specified attribute.",
                },
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "get_order_by_id",
        "description": "Retrieves the details of a specific order based on the order ID. Returns the order ID, product name, quantity, price, and order status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The unique identifier for the order.",
                }
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "get_customer_orders",
        "description": "Retrieves the list of orders belonging to a user based on a user's customer id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "The customer_id belonging to the user",
                }
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "cancel_order",
        "description": "Cancels an order based on a provided order_id. Only orders that are 'processing' can be cancelled.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order_id pertaining to a particular order",
                }
            },
            "required": ["order_id"],
        },
    },
]

# --- Agent Logic ---

db = FakeDatabase()
client = anthropic.Anthropic()


def process_tool_call(tool_name, tool_input):
    if tool_name == "get_user":
        return db.get_user(tool_input["key"], tool_input["value"])
    elif tool_name == "get_order_by_id":
        return db.get_order_by_id(tool_input["order_id"])
    elif tool_name == "get_customer_orders":
        return db.get_customer_orders(tool_input["customer_id"])
    elif tool_name == "cancel_order":
        return db.cancel_order(tool_input["order_id"])


def run_agent_turn(messages, max_tool_rounds=10):
    """Run the agent loop for a single user turn. Returns the final text response."""
    for _ in range(max_tool_rounds):
        response = client.messages.create(
            model=MODEL_NAME,
            system=SYSTEM_PROMPT,
            max_tokens=4096,
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            tool_use = next(b for b in response.content if b.type == "tool_use")
            tool_name = tool_use.name
            tool_input = tool_use.input
            print(f"  [tool] {tool_name}({json.dumps(tool_input)})")

            tool_result = process_tool_call(tool_name, tool_input)

            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": json.dumps(tool_result, default=str),
                    }
                ],
            })
        else:
            # Extract text response
            text = response.content[0].text if response.content else ""
            return text

    return "[max tool rounds exceeded]"


# --- Test Scenarios ---

TEST_SCENARIOS = [
    {
        "name": "lookup_and_list_orders",
        "description": "User asks to look up orders by email",
        "conversation": [
            "Can you look up my orders? My email is john@gmail.com",
        ],
    },
    {
        "name": "cancel_processing_order",
        "description": "User wants to cancel an order that is still processing",
        "conversation": [
            "Hi, I'd like to cancel an order. My username is priya123",
            "Yes, please cancel order 28164",
        ],
    },
    {
        "name": "cancel_shipped_order",
        "description": "User tries to cancel an already-shipped order (should fail gracefully)",
        "conversation": [
            "I need to cancel order 24601. My email is john@gmail.com",
        ],
    },
    {
        "name": "vague_request",
        "description": "User gives a vague request — agent should ask for more info",
        "conversation": [
            "I want to check on my order",
        ],
    },
    {
        "name": "multi_step_lookup",
        "description": "User asks about a specific product across multiple turns",
        "conversation": [
            "What's the status of the smartwatch order? The customer username is manderson",
        ],
    },
]


def run_test_scenarios():
    """Run all test scenarios, each in its own traced session."""
    print(f"Running {len(TEST_SCENARIOS)} test scenarios...\n")

    for scenario in TEST_SCENARIOS:
        name = scenario["name"]
        print(f"--- Scenario: {name} ---")
        print(f"    {scenario['description']}")

        # Fresh DB per scenario so cancellations don't leak
        global db
        db = FakeDatabase()

        with ri.session(traces_dir="./eval/traces", session_id=name, metadata={"scenario": name}) as s:
            messages = []
            final_response = None

            for user_msg in scenario["conversation"]:
                print(f"  User: {user_msg}")
                messages.append({"role": "user", "content": user_msg})
                final_response = run_agent_turn(messages)
                print(f"  Agent: {final_response}\n")

            s.finish(output=final_response, success=True)

        print()

    print("Done! Traces saved to ./traces/")


def interactive_chat():
    """Run an interactive chat session with trace capture."""
    print("TechNova Customer Support (type 'quit' to exit)")
    print("=" * 50)

    with ri.session(traces_dir="./eval/traces", session_id="interactive") as s:
        messages = []
        while True:
            try:
                user_input = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if user_input.lower() in ("quit", "exit", "q"):
                break

            messages.append({"role": "user", "content": user_input})
            response = run_agent_turn(messages)
            print(f"\nTechNova Support: {response}")

        s.finish(output="chat ended", success=True)

    print("\nTrace saved to ./traces/")


if __name__ == "__main__":
    ri.patch()

    if "--interactive" in sys.argv:
        interactive_chat()
    else:
        run_test_scenarios()
