# 测试客服机器人工作流文档 docs/customer_service_agent.md
# 机器人工作流：
# 1. 用户背景信息查询和问题分类并行执行
# 2. 推理回答、自我检查、拟人回复串行执行
# 3. 问题分类细化
# 4. 拟人化语言风格

from datetime import datetime
from typing import Literal, List, Dict, Any, Optional, Annotated
import re
import operator
import logging

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda, RunnableSerializable
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, MessagesState, StateGraph, START

from core import get_model, settings
from schema import ChatMessage
from tools.user_info import get_user_summary, get_user_orders, get_user_parcels

# 使用全局logging，不需要单独的logger配置

# Define dictionary merge function for parallel execution
def merge_dicts(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    """Merge two dictionaries, with right dict taking precedence for overlapping keys."""
    if left is None:
        return right
    if right is None:
        return left
    result = left.copy()
    result.update(right)
    return result

class CustomerServiceState(MessagesState, total=False):
    """State for customer service agent."""
    # 使用自定义的merge_dicts函数作为reducer，支持并行执行时的状态合并
    categories: Annotated[Dict[str, Any], merge_dicts]
    userInfo: Optional[Dict[str, Any]]
    userToken: Optional[str]
    backgroundInfo: Annotated[Dict[str, Any], merge_dicts]  # 用户背景信息
    reasoning_response: Optional[str]  # 推理回答
    humanized_response: Optional[str]  # 拟人回复
    self_check_passed: Optional[bool]  # 自我检查结果
    check_count: Optional[int]  # 检查次数


async def categorize_message(message: str) -> Dict[str, Any]:
    """Categorize the customer message to determine intent."""
    # 根据文档要求的5个具体分类
    categories = {
        "new_user_onboarding": ["注册", "登录", "怎么使用", "如何使用", "新用户", "第一次", "教程", "guide", "register", "login", "how to use", "first time", "tutorial"],
        "payment_issues": ["支付", "付款", "充值", "余额", "手续费", "转账", "revolut", "wise", "payment", "pay", "top up", "balance", "fee", "transfer"],
        "order_issues": ["订单", "多久到仓库", "多久到家", "质检照片", "退货", "order", "warehouse", "delivery time", "quality check", "return", "refund"],
        "parcel_issues": ["包裹", "运费", "包裹状态", "被没收", "tracking", "parcel", "shipping cost", "parcel status", "confiscated"],
        "other_issues": ["平台介绍", "平台政策", "平台活动", "其他", "介绍", "政策", "活动", "platform", "policy", "activity", "other"]
    }
    
    message_lower = message.lower()
    detected_categories = []
    
    # 首先尝试关键词匹配
    for category, keywords in categories.items():
        if any(keyword in message_lower for keyword in keywords):
            detected_categories.append(category)
    
    # 如果关键词匹配成功，直接返回结果
    if detected_categories:
        return {
            "categories": detected_categories,
            "confidence": 0.9,
            "method": "keyword_matching"
        }
    
    # 如果没有关键词命中，使用大模型推理
    try:
        model = get_model(settings.DEFAULT_MODEL)
        
        classification_prompt = f"""请分析以下客户消息，将其分类到最合适的类别中。
            客户消息：{message}
            可选分类：
            1. new_user_onboarding - 新用户引导（注册、登录、如何使用平台、教程等）
            2. payment_issues - 支付问题（支付、充值、余额、手续费、转账等）
            3. order_issues - 订单问题（订单状态、到货时间、质检、退货等）
            4. parcel_issues - 包裹问题（包裹状态、运费、物流、被没收等）
            5. other_issues - 其他问题（平台介绍、政策、活动等）
            请只返回分类名称，不要包含其他内容。如果消息涉及多个类别，请选择最主要的一个。
            分类结果："""

        messages = [
            SystemMessage(content=classification_prompt),
            HumanMessage(content="请进行分类")
        ]
        
        response = await model.ainvoke(messages)
        llm_category = response.content.strip().lower()
        
        # 验证LLM返回的分类是否有效
        valid_categories = list(categories.keys())
        if llm_category in valid_categories:
            detected_categories = [llm_category]
            confidence = 0.7  # LLM推理的置信度稍低
        else:
            # 如果LLM返回的分类无效，默认为other_issues
            detected_categories = ["other_issues"]
            confidence = 0.5
            
        return {
            "categories": detected_categories,
            "confidence": confidence,
            "method": "llm_inference",
            "llm_raw_response": llm_category
        }
        
    except Exception as e:
        logging.warning(f"LLM inference failed for message categorization: {e}")
        # 如果LLM推理失败，默认为other_issues
        return {
            "categories": ["other_issues"],
            "confidence": 0.3,
            "method": "fallback"
        }

async def categorize_customer_message(state: CustomerServiceState, config: RunnableConfig) -> CustomerServiceState:
    """Categorize the customer message to determine intent."""
    last_message = state["messages"][-1]
    if not isinstance(last_message, HumanMessage):
        return {"categories": {}}
        
    categories = await categorize_message(last_message.content)
    return {"categories": categories}

async def get_user_information(state: CustomerServiceState, config: RunnableConfig) -> CustomerServiceState:
    """Get user information using userToken from config."""
    try:
        # 从config中获取user_token
        userToken = config.get("configurable", {}).get("userToken")
        logging.info(f"User token: {userToken}")
        logging.info(f"User configurable: {config.get('configurable', {})}")
        logging.info(f"User config: {config}")
        if not userToken:
            return {
                "userInfo": None, 
                "userToken": None, 
                "backgroundInfo": {
                    "userInfo": None,
                    "orders": [],
                    "parcels": []
                }
            }
        
        # 获取用户信息
        userInfo = await get_user_summary(userToken)
        
        logging.info(f"User token: {userToken}")
        logging.info(f"User info: {userInfo}")
        
        # 并发获取用户订单和包裹信息
        backgroundInfo = {
        
            "userInfo": userInfo,
            "orders": [],
            "parcels": []
        }
        
        try:
            orders = await get_user_orders(userToken)
            backgroundInfo["orders"] = [order.dict() for order in orders]
        except Exception as e:
            logging.warning(f"Failed to get user orders: {e}")
            
        try:
            parcels = await get_user_parcels(userToken)
            backgroundInfo["parcels"] = [parcel.dict() for parcel in parcels]
        except Exception as e:
            logging.warning(f"Failed to get user parcels: {e}")
        
        return {
            "userInfo": userInfo, 
            "userToken": userToken,
            "backgroundInfo": backgroundInfo
        }
        
    except Exception as e:
        # 如果获取用户信息失败，记录错误但继续处理
        logging.error(f"Failed to get user info: {e}")
        return {
            "userInfo": None, 
            "userToken": userToken, 
            "backgroundInfo": {
                "userInfo": None,
                "orders": [],
                "parcels": []
            }
        }

async def reasoning_response(state: CustomerServiceState, config: RunnableConfig) -> CustomerServiceState:
    """推理回答：基于背景信息和问题分类进行深度推理"""
    m = get_model(config["configurable"].get("model", settings.DEFAULT_MODEL))
    
    # 构建推理提示
    user_message = state["messages"][-1].content if state["messages"] else ""
    categories = state.get("categories", {}).get("categories", [])
    backgroundInfo = state.get("backgroundInfo", {})
    
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    reasoning_prompt = f"""基于以下信息进行深度推理，生成专业、准确的回答：
当前时间：{current_time}
用户问题：{user_message}
问题分类：{categories}
用户背景信息：{backgroundInfo}

请根据问题分类提供相应的专业回答：

1. 新用户onboarding：详细说明注册流程、平台使用方法、购物车制作、支付流程
2. 支付问题：解释支付渠道、手续费、充值流程
3. 订单问题：说明订单处理时间、质检流程、退货政策
4. 包裹问题：解释运费构成、配送时间、包裹状态查询、异常处理
5. 其他问题：提供平台介绍、政策说明、活动信息

要求：
- 回答要准确、专业、完整
- 基于用户背景信息提供个性化建议
- 提供具体的操作步骤和解决方案
"""
    
    messages = [
        SystemMessage(content=reasoning_prompt),
        HumanMessage(content=user_message)
    ]
    
    response = await m.ainvoke(messages, config)
    
    # 重置检查次数，开始新的推理
    return {"reasoning_response": response.content}

async def self_check_response(state: CustomerServiceState, config: RunnableConfig) -> CustomerServiceState:
    """回答自我检查：检查回答是否准确、完整、无幻觉，最多检查2次"""
    check_count = state.get("check_count", 0)
    if check_count >= 1:
        logging.info(f"Self check passed after {check_count} attempts (max limit reached)")
        return {"self_check_passed": True, "check_count": check_count}
    
    m = get_model(config["configurable"].get("model", settings.DEFAULT_MODEL))
    
    reasoning_response = state.get("reasoning_response", "")
    user_message = state["messages"][-1].content if state["messages"] else ""
    
    # 获取当前检查次数，默认为0
    
    
    check_prompt = f"""请对以下客服回答进行自我检查：

用户问题：{user_message}
客服回答：{reasoning_response}

检查标准：
1. 是否准确回答了用户的问题,聚焦解决用户当前问题，不要扩展其他问题
2. 是否有事实错误或幻觉，不要编造事实
3. 是否违背常识
4. 不需要检查用户身份，不要询问用户身份，系统会自动获取用户身份和订单信息给之前的模型

请给出检查结果：
- 如果检查通过，回复"PASS"
- 如果检查不通过，回复"FAIL"并说明原因

检查结果："""
    
    messages = [
        SystemMessage(content=check_prompt),
        HumanMessage(content="请进行自我检查")
    ]
    
    response = await m.ainvoke(messages, config)
    check_result = response.content.strip()
    
    if "PASS" in check_result.upper():
        return {"self_check_passed": True}
    else:
        # 检查不通过，增加检查次数
        new_check_count = check_count + 1
        logging.warning(f"Self check failed (attempt {new_check_count}/2): {check_result}")
        
        # 如果达到2次检查，直接通过
        if new_check_count >= 2:
            logging.info(f"Self check passed after {new_check_count} attempts (max limit reached)")
            return {"self_check_passed": True, "check_count": new_check_count}
        else:
            return {"self_check_passed": False, "check_feedback": check_result, "check_count": new_check_count}

async def humanize_response(state: CustomerServiceState, config: RunnableConfig) -> CustomerServiceState:
    """拟人回复：将专业回答转换为自然、口语化的语言"""
    m = get_model(config["configurable"].get("model", settings.DEFAULT_MODEL))
    
    reasoning_response = state.get("reasoning_response", "")
    user_message = state["messages"][-1].content if state["messages"] else ""
    backgroundInfo = state.get("backgroundInfo", {})
    
    humanize_prompt = f"""请将以下专业回答转换为自然、口语化的拟人回复：

原始回答：{reasoning_response}
用户问题：{user_message}

拟人化要求：
1. 使用自然、口语化的语言，避免生硬、机械的表述
2. 避免过于专业的术语，使用日常交流习惯的表达
3. 根据问题性质融入积极的情感元素：
   - 解决问题后表达"很高兴能为您解决这个问题"
   - 用户遇到困扰时表达"非常理解您的心情"
4. 适当添加语气词（如"呢""啦""哦"）和表情符号（😊、👍等）
5. 使用第一人称（我、我们）和第二人称（您）增强互动感
6. 根据用户背景信息进行个性化调整
7. 在结尾添加友好的互动语句，如"如果您还有其他疑问，欢迎随时向我咨询哦～"

注意：
- 保持专业性，避免过度使用表情符号
- 确保信息准确性和完整性
- 使用"\n\n"分隔长消息

拟人化回复："""
    
    messages = [
        SystemMessage(content=humanize_prompt),
        HumanMessage(content="请进行拟人化处理")
    ]
    
    response = await m.ainvoke(messages, config)
    return {"humanized_response": response.content}

async def final_response(state: CustomerServiceState, config: RunnableConfig) -> CustomerServiceState:
    """最终回复：将拟人化回复添加到消息中"""
    humanized_response = state.get("humanized_response", "")
    if humanized_response:
        return {"messages": [AIMessage(content=humanized_response)]}
    else:
        # 如果没有拟人化回复，使用推理回复
        reasoning_response = state.get("reasoning_response", "")
        return {"messages": [AIMessage(content=reasoning_response)]}

# Define the graph
agent = StateGraph(CustomerServiceState)

# Add nodes
agent.add_node("get_user_info", get_user_information)
agent.add_node("category_analyzer", categorize_customer_message)
agent.add_node("reasoning", reasoning_response)
agent.add_node("self_check", self_check_response)
agent.add_node("humanize", humanize_response)
agent.add_node("final_response", final_response)

# 并行执行：用户背景信息查询和问题分类
# 从START开始，同时执行get_user_info和category_analyzer
agent.add_edge(START, "get_user_info")
agent.add_edge(START, "category_analyzer")

# 串行执行：推理回答、自我检查、拟人回复
agent.add_edge("get_user_info", "reasoning")
agent.add_edge("category_analyzer", "reasoning")
agent.add_edge("reasoning", "self_check")

# 条件分支：如果自我检查通过，继续拟人化；否则重新推理
def should_continue(state: CustomerServiceState) -> str:
    """决定是否继续到拟人化步骤"""
    if state.get("self_check_passed", False):
        return "humanize"
    else:
        return "reasoning"  # 重新推理

agent.add_conditional_edges("self_check", should_continue)

agent.add_edge("humanize", "final_response")
agent.add_edge("final_response", END)

# Compile the agent with parallel execution
customer_service_agent = agent.compile(
    checkpointer=MemorySaver(),
)
customer_service_agent.name = "customer-service-agent"


# 使用示例
async def example_customer_service_usage():
    """客服智能体使用示例"""
    
    # 模拟用户消息
    user_message = "Hi, I want to check my order status"
    userToken = "example_user_token_123"
    
    # 创建初始状态
    initial_state = {
        "messages": [HumanMessage(content=user_message)]
    }
    
    # 配置，包含user_token
    config = {
        "configurable": {
            "userToken": userToken,
            "model": settings.DEFAULT_MODEL
        }
    }
    
    try:
        # 运行智能体
        result = await customer_service_agent.ainvoke(initial_state, config)
        
        # 获取AI回复
        ai_messages = [msg for msg in result["messages"] if isinstance(msg, AIMessage)]
        
        logging.info("Customer Service Agent Response:")
        for msg in ai_messages:
            logging.info(f"AI: {msg.content}")
            
        # 打印用户信息（如果获取成功）
        if result.get("userInfo"):
            userInfo = result["userInfo"]
            logging.info(f"\nUser Info Retrieved:")
            logging.info(f"- Email: {userInfo.email}")
            logging.info(f"- VIP Level: {userInfo.vip_level}")
            logging.info(f"- Balance: {userInfo.balance_cny} CNY")
            
        # 打印并行执行的结果
        logging.info(f"\nParallel Execution Results:")
        logging.info(f"- Categories: {result.get('categories', {})}")
        logging.info(f"- Background Info: {result.get('backgroundInfo', {})}")
            
    except Exception as e:
        logging.info(f"Error running customer service agent: {e}")

# 测试并行执行
async def test_parallel_execution():
    """测试并行执行是否正常工作"""
    logging.info("Testing parallel execution of get_user_info and category_analyzer...")
    
    # 模拟用户消息
    user_message = "How much is shipping to Germany?"
    userToken = "test_user_token"
    
    # 创建初始状态
    initial_state = {
        "messages": [HumanMessage(content=user_message)]
    }
    
    # 配置
    config = {
        "configurable": {
            "userToken": userToken,
            "model": settings.DEFAULT_MODEL
        }
    }
    
    try:
        # 运行智能体
        result = await customer_service_agent.ainvoke(initial_state, config)
        
        logging.info("✅ Parallel execution test completed!")
        logging.info(f"Categories: {result.get('categories', {})}")
        logging.info(f"User Info: {result.get('userInfo', 'Not retrieved')}")
        logging.info(f"Background Info: {result.get('backgroundInfo', {})}")
        
        return True
        
    except Exception as e:
        logging.info(f"❌ Parallel execution test failed: {e}")
        return False
