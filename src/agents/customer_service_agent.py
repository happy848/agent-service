from datetime import datetime
from typing import Literal, List, Dict, Any
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda, RunnableSerializable
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import create_react_agent

from core import get_model, settings
from schema import ChatMessage

# Define tools for customer service agent
def analyze_sentiment(message: str) -> Dict[str, Any]:
    """Analyze the sentiment of a message."""
    # Simple sentiment analysis - can be replaced with more sophisticated model
    positive_words = ["thank", "good", "great", "awesome", "excellent", "happy", "love", "perfect"]
    negative_words = ["bad", "poor", "terrible", "worst", "angry", "upset", "disappointed", "hate"]
    
    message = message.lower()
    sentiment = "neutral"
    
    if any(word in message for word in positive_words):
        sentiment = "positive"
    elif any(word in message for word in negative_words):
        sentiment = "negative"
        
    return {
        "sentiment": sentiment,
        "confidence": 0.8  # Placeholder confidence score
    }

def get_order_status(order_id: str) -> Dict[str, Any]:
    """Get the status of an order."""
    # Mock order status - replace with actual database query
    return {
        "status": "processing",
        "estimated_delivery": "2024-03-20",
        "tracking_number": "ABC123XYZ"
    }

def calculate_shipping_cost(weight_kg: float, destination: str) -> Dict[str, Any]:
    """Calculate shipping cost based on weight and destination."""
    # Mock shipping calculation - replace with actual logic
    base_rate = 25.0  # Base rate for first kg
    additional_rate = 12.0  # Rate per additional kg
    
    total_cost = base_rate + (weight_kg - 1) * additional_rate if weight_kg > 1 else base_rate
    
    return {
        "base_rate": base_rate,
        "total_cost": round(total_cost, 2),
        "currency": "USD",
        "estimated_days": "8-11"
    }

def categorize_message(message: str) -> Dict[str, Any]:
    """Categorize the customer message to determine intent."""
    # Categories based on the ordering guide
    categories = {
        "ordering": ["how to order", "place order", "buy", "purchase", "shopping", "cart"],
        "shipping": ["shipping", "delivery", "track", "tracking", "when will", "receive"],
        "payment": ["payment", "pay", "top up", "balance", "transfer", "revolut", "wise"],
        "refund": ["refund", "return", "money back", "cancel"],
        "product": ["product", "item", "goods", "link", "url", "find"],
        "general": ["hello", "hi", "help", "support", "question", "contact"]
    }
    
    message = message.lower()
    detected_categories = []
    
    for category, keywords in categories.items():
        if any(keyword in message for keyword in keywords):
            detected_categories.append(category)
    
    if not detected_categories:
        detected_categories = ["general"]
        
    return {
        "categories": detected_categories,
        "confidence": 0.9  # Placeholder confidence score
    }

class CustomerServiceState(MessagesState, total=False):
    """State for customer service agent."""
    sentiment: Dict[str, Any]
    categories: Dict[str, Any]

def wrap_model(model: BaseChatModel) -> RunnableSerializable[CustomerServiceState, AIMessage]:
    """Wrap the model with system prompt and state handling."""
    system_prompt = """You are a professional customer service agent for an international purchasing agency.
    Your role is to assist customers with their orders, shipping inquiries, and general questions.
    Always be polite, professional, and helpful. Match the language of the customer in your responses.
    
    Key business points:
    1. We help customers purchase products internationally
    2. Shipping typically takes 8-11 working days
    3. Base shipping rate is $25 for first kg, $12 for each additional kg
    4. We accept payments via bank transfer (SEPA/SWIFT) or Revolut/Wise
    5. All prices are in USD unless specified otherwise
    
    Ordering Process:
    1. Customer pastes product link into our search bar
    2. They select product options and add to cart
    3. Submit order and top up balance
    4. We purchase and ship to warehouse
    5. Customer creates shipping parcel
    6. Pay shipping fee and we deliver internationally
    
    WhatsApp Message Guidelines:
    - Keep each message under 300 characters when possible
    - For long explanations, split into multiple shorter messages
    - Use line breaks to improve readability
    - Start new messages for new topics or steps
    - Use emojis sparingly but effectively
    - End each message with a clear call to action
    
    Remember to:
    - Be concise and direct - WhatsApp users prefer shorter messages
    - Use bullet points for lists
    - Break down complex information into digestible chunks
    - Show empathy when dealing with issues
    - Maintain a professional but friendly tone
    - Reference previous messages when relevant
    - Acknowledge time gaps appropriately
    - Add friendly emojis to seem more human-like
    - Use casual, conversational language while maintaining professionalism
    - NEVER include timestamps in your responses
    - ALWAYS use "\n\n" to indicate where a message should be split into separate WhatsApp messages"""
    
    preprocessor = RunnableLambda(
        lambda state: [SystemMessage(content=system_prompt)] + state["messages"],
        name="StateModifier",
    )
    return preprocessor | model

async def analyze_message_sentiment(state: CustomerServiceState, config: RunnableConfig) -> CustomerServiceState:
    """Analyze sentiment of the last user message."""
    last_message = state["messages"][-1]
    if not isinstance(last_message, HumanMessage):
        return state
        
    sentiment = analyze_sentiment(last_message.content)
    return {"sentiment": sentiment}

async def categorize_customer_message(state: CustomerServiceState, config: RunnableConfig) -> CustomerServiceState:
    """Categorize the customer message to determine intent."""
    last_message = state["messages"][-1]
    if not isinstance(last_message, HumanMessage):
        return state
        
    categories = categorize_message(last_message.content)
    return {"categories": categories}

async def acall_model(state: CustomerServiceState, config: RunnableConfig) -> CustomerServiceState:
    """Process messages through the model."""
    m = get_model(config["configurable"].get("model", settings.DEFAULT_MODEL))
    model_runnable = wrap_model(m)
    response = await model_runnable.ainvoke(state, config)
    
    return {"messages": [response]}

# Create React agent with tools
tools = [
    analyze_sentiment,
    get_order_status,
    calculate_shipping_cost,
    categorize_message
]

react_agent = create_react_agent(
    model=get_model(settings.DEFAULT_MODEL),
    tools=tools,
    name="customer_service",
    prompt="""You are a professional customer service agent for an international purchasing agency.
    Use the available tools to help customers with their inquiries.
    Always maintain a professional and helpful tone while being conversational and human-like.
    
    Customer Service Workflow:
    1. Analyze message sentiment and category to understand customer's needs
    2. For ordering questions:
       - Guide through the 6-step ordering process
       - Explain supported platforms
       - Provide clear instructions for each step
    3. For shipping inquiries:
       - Check order status if tracking number provided
       - Calculate shipping costs if weight/destination given
       - Explain 8-11 working days delivery time
    4. For payment questions:
       - Explain top-up process
       - List payment methods (SEPA/SWIFT/Revolut/Wise)
       - Mention USD as default currency
    5. For refund requests:
       - Check if items are still in warehouse (4-day window)
       - Explain refund policy and fees
    6. For general inquiries:
       - Provide relevant information from our knowledge base
       - Direct to specific guides when applicable
    
    WhatsApp Message Guidelines:
    - Keep each message under 200 characters
    - Use "\n\n" to split long responses into separate messages
    - Start new messages for different topics
    - Use emojis sparingly but effectively
    - End each message with a clear next step
    
    Remember to:
    - Match customer's language style
    - Use appropriate emojis to seem more human
    - Show empathy for issues
    - Be clear about timeframes
    - Break down complex processes into simple steps
    - End with a friendly, helpful tone""")

# Define the graph
agent = StateGraph(CustomerServiceState)

# Add nodes
agent.add_node("sentiment_analyzer", analyze_message_sentiment)
agent.add_node("category_analyzer", categorize_customer_message)
agent.add_node("react", react_agent)
agent.add_node("model", acall_model)

# Set entry point
agent.set_entry_point("sentiment_analyzer")

# Add edges
agent.add_edge("sentiment_analyzer", "category_analyzer")
agent.add_edge("category_analyzer", "react")
agent.add_edge("react", "model")
agent.add_edge("model", END)

# Compile the agent
customer_service_agent = agent.compile(
    checkpointer=MemorySaver(),
)
customer_service_agent.name = "customer-service-agent"
