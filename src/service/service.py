import json
import logging
import warnings
import asyncio
import time
from asyncio import CancelledError
from builtins import GeneratorExit
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Dict, Optional, List
from uuid import UUID, uuid4
from datetime import datetime

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langchain_core._api import LangChainBetaWarning
from langchain_core.messages import AIMessage, AIMessageChunk, AnyMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Interrupt
from langsmith import Client as LangsmithClient
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

from agents import DEFAULT_AGENT, get_agent, get_all_agent_info
from core import settings
from memory import initialize_database
from schema import (
    ChatHistory,
    ChatHistoryInput,
    ChatMessage,
    Feedback,
    FeedbackResponse,
    ServiceMetadata,
    StreamInput,
    UserInput,
    WhatsAppContactInput,
    WhatsAppMessageInput,
)
from service.utils import (
    convert_message_content_to_string,
    langchain_to_chat_message,
    remove_tool_calls,
    log_performance_metrics,
    performance_metrics,
)

from tools.api_taobao import api_taobao
from tools.user_info import get_user_summary

# 导入WhatsApp模块
# from brain import whatsapp

warnings.filterwarnings("ignore", category=LangChainBetaWarning)

LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s:%(lineno)d] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=DATE_FORMAT,
    handlers=[logging.StreamHandler()]
)

def verify_bearer(
    http_auth: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(HTTPBearer(description="Please provide AUTH_SECRET api key.", auto_error=False)),
    ],
) -> None:
    if not settings.AUTH_SECRET:
        return
    auth_secret = settings.AUTH_SECRET.get_secret_value()
    if not http_auth or http_auth.credentials != auth_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


class TimingMiddleware(BaseHTTPMiddleware):
    """中间件：记录请求处理时间"""
    
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        # 处理请求
        response = await call_next(request)
        
        # 计算处理时间
        process_time = time.time() - start_time
        
        # 添加处理时间到响应头
        response.headers["X-Process-Time"] = str(process_time)
        response.headers["X-Process-Time-MS"] = str(int(process_time * 1000))
        
        # 记录性能指标
        log_performance_metrics(
            request_path=str(request.url.path),
            method=request.method,
            status_code=response.status_code,
            process_time=process_time,
            additional_metrics={
                "query_params": str(request.query_params),
                "client_ip": request.client.host if request.client else "unknown"
            }
        )
        
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Configurable lifespan that initializes the appropriate database checkpointer based on settings.
    """
    try:
        # 初始化数据库
        async with initialize_database() as saver:
            await saver.setup()
            agents = get_all_agent_info()
            for a in agents:
                agent = get_agent(a.key)
                agent.checkpointer = saver
            
            # 启动浏览器服务（常驻浏览器）
            from client.whatsapp_client import global_whatsapp_client
            await global_whatsapp_client.start()
            
            yield
    except Exception as e:
        logging.error(f"Error during database initialization: {e}")
        raise
    finally:
        # 停止浏览器服务
        try:
            from client.whatsapp_client import global_whatsapp_client
            await global_whatsapp_client.stop()
            logging.info("浏览器服务已停止")
        except Exception as e:
            logging.error(f"Error stopping browser service: {e}")
            

app = FastAPI(
    lifespan=lifespan,
    # 设置请求超时时间为5分钟
    timeout=300
)

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 添加请求耗时中间件
app.add_middleware(TimingMiddleware)

# 添加超时中间件
@app.middleware("http")
async def timeout_middleware(request: Request, call_next):
    try:
        return await asyncio.wait_for(call_next(request), timeout=300)
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=504,
            content={"detail": "Request timeout"}
        )

router = APIRouter(dependencies=[Depends(verify_bearer)])


@router.get("/info")
async def info() -> ServiceMetadata:
    models = list(settings.AVAILABLE_MODELS)
    models.sort()
    return ServiceMetadata(
        agents=get_all_agent_info(),
        models=models,
        default_agent=DEFAULT_AGENT,
        default_model=settings.DEFAULT_MODEL,
    )


async def _handle_input(
    user_input: UserInput, agent: CompiledStateGraph
) -> tuple[dict[str, Any], UUID]:
    """
    Parse user input and handle any required interrupt resumption.
    Returns kwargs for agent invocation and the run_id.
    """
    run_id = uuid4()
    thread_id = user_input.thread_id or str(uuid4())

    configurable = {"thread_id": thread_id, "model": user_input.model}

    if user_input.agent_config:
        overlap = configurable.keys() & user_input.agent_config.keys()
        if overlap:
            raise HTTPException(
                status_code=422,
                detail=f"agent_config contains reserved keys: {overlap}",
            )
        configurable.update(user_input.agent_config)

    config = RunnableConfig(
        configurable=configurable,
        run_id=run_id,
    )

    # Check for interrupts that need to be resumed
    state = await agent.aget_state(config=config)
    interrupted_tasks = [
        task for task in state.tasks if hasattr(task, "interrupts") and task.interrupts
    ]

    if interrupted_tasks:
        # assume user input is response to resume agent execution from interrupt
        input = Command(resume=user_input.message)
    else:
        input = {"messages": [HumanMessage(content=user_input.message)]}

    kwargs = {
        "input": input,
        "config": config,
    }

    return kwargs, run_id


@router.post("/{agent_id}/invoke")
@router.post("/invoke")
async def invoke(user_input: UserInput, agent_id: str = DEFAULT_AGENT) -> ChatMessage:
    """
    Invoke an agent with user input to retrieve a final response.

    If agent_id is not provided, the default agent will be used.
    Use thread_id to persist and continue a multi-turn conversation. run_id kwarg
    is also attached to messages for recording feedback.
    """
    # NOTE: Currently this only returns the last message or interrupt.
    # In the case of an agent outputting multiple AIMessages (such as the background step
    # in interrupt-agent, or a tool step in research-assistant), it's omitted. Arguably,
    # you'd want to include it. You could update the API to return a list of ChatMessages
    # in that case.
    agent: CompiledStateGraph = get_agent(agent_id)
    kwargs, run_id = await _handle_input(user_input, agent)
    try:
        response_events = await agent.ainvoke(**kwargs, stream_mode=["updates", "values"])
        response_type, response = response_events[-1]
        if response_type == "values":
            # Normal response, the agent completed successfully
            output = langchain_to_chat_message(response["messages"][-1])
        elif response_type == "updates" and "__interrupt__" in response:
            # The last thing to occur was an interrupt
            # Return the value of the first interrupt as an AIMessage
            output = langchain_to_chat_message(
                AIMessage(content=response["__interrupt__"][0].value)
            )
        else:
            raise ValueError(f"Unexpected response type: {response_type}")

        output.run_id = str(run_id)
        return output
    except Exception as e:
        logging.error(f"An exception occurred: {e}")
        raise HTTPException(status_code=500, detail="Unexpected error")


async def message_generator(
    user_input: StreamInput, agent_id: str = DEFAULT_AGENT
) -> AsyncGenerator[str, None]:
    """
    Generate a stream of messages from the agent.

    This is the workhorse method for the /stream endpoint.
    """
    agent: CompiledStateGraph = get_agent(agent_id)
    kwargs, run_id = await _handle_input(user_input, agent)

    try:
        # Process streamed events from the graph and yield messages over the SSE stream.
        async for stream_event in agent.astream(
            **kwargs, stream_mode=["updates", "messages", "custom"]
        ):
            if not isinstance(stream_event, tuple):
                continue
            stream_mode, event = stream_event
            new_messages = []
            if stream_mode == "updates":
                for node, updates in event.items():
                    # A simple approach to handle agent interrupts.
                    # In a more sophisticated implementation, we could add
                    # some structured ChatMessage type to return the interrupt value.
                    if node == "__interrupt__":
                        interrupt: Interrupt
                        for interrupt in updates:
                            new_messages.append(AIMessage(content=interrupt.value))
                        continue
                    update_messages = updates.get("messages", [])
                    # special cases for using langgraph-supervisor library
                    if node == "supervisor":
                        # Get only the last AIMessage since supervisor includes all previous messages
                        ai_messages = [msg for msg in update_messages if isinstance(msg, AIMessage)]
                        if ai_messages:
                            update_messages = [ai_messages[-1]]
                    if node in ("research_expert", "math_expert"):
                        # By default the sub-agent output is returned as an AIMessage.
                        # Convert it to a ToolMessage so it displays in the UI as a tool response.
                        msg = ToolMessage(
                            content=update_messages[0].content,
                            name=node,
                            tool_call_id="",
                        )
                        update_messages = [msg]
                    new_messages.extend(update_messages)

            if stream_mode == "custom":
                new_messages = [event]

            for message in new_messages:
                try:
                    chat_message = langchain_to_chat_message(message)
                    chat_message.run_id = str(run_id)
                except Exception as e:
                    logging.error(f"Error parsing message: {e}")
                    yield f"data: {json.dumps({'type': 'error', 'content': 'Unexpected error'})}\n\n"
                    continue
                # LangGraph re-sends the input message, which feels weird, so drop it
                if chat_message.type == "human" and chat_message.content == user_input.message:
                    continue
                yield f"data: {json.dumps({'type': 'message', 'content': chat_message.model_dump()})}\n\n"

            if stream_mode == "messages":
                if not user_input.stream_tokens:
                    continue
                msg, metadata = event
                if "skip_stream" in metadata.get("tags", []):
                    continue
                # For some reason, astream("messages") causes non-LLM nodes to send extra messages.
                # Drop them.
                if not isinstance(msg, AIMessageChunk):
                    continue
                content = remove_tool_calls(msg.content)
                if content:
                    # Empty content in the context of OpenAI usually means
                    # that the model is asking for a tool to be invoked.
                    # So we only print non-empty content.
                    yield f"data: {json.dumps({'type': 'token', 'content': convert_message_content_to_string(content)})}\n\n"
    except GeneratorExit:
        # Handle GeneratorExit gracefully
        logging.info("Stream closed by client")
        return
    except CancelledError:
        # Handle CancelledError gracefully
        logging.info("Stream cancelled")
        return
    except Exception as e:
        logging.error(f"Error in message generator: {e}")
        yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
    finally:
        yield "data: [DONE]\n\n"


def _sse_response_example() -> dict[int, Any]:
    return {
        status.HTTP_200_OK: {
            "description": "Server Sent Event Response",
            "content": {
                "text/event-stream": {
                    "example": "data: {'type': 'token', 'content': 'Hello'}\n\ndata: {'type': 'token', 'content': ' World'}\n\ndata: [DONE]\n\n",
                    "schema": {"type": "string"},
                }
            },
        }
    }


@router.post(
    "/{agent_id}/stream",
    response_class=StreamingResponse,
    responses=_sse_response_example(),
)
@router.post("/stream", response_class=StreamingResponse, responses=_sse_response_example())
async def stream(user_input: StreamInput, agent_id: str = DEFAULT_AGENT) -> StreamingResponse:
    """
    Stream an agent's response to a user input, including intermediate messages and tokens.

    If agent_id is not provided, the default agent will be used.
    Use thread_id to persist and continue a multi-turn conversation. run_id kwarg
    is also attached to all messages for recording feedback.

    Set `stream_tokens=false` to return intermediate messages but not token-by-token.
    """
    return StreamingResponse(
        message_generator(user_input, agent_id),
        media_type="text/event-stream",
    )


@router.post("/feedback")
async def feedback(feedback: Feedback) -> FeedbackResponse:
    """
    Record feedback for a run to LangSmith.

    This is a simple wrapper for the LangSmith create_feedback API, so the
    credentials can be stored and managed in the service rather than the client.
    See: https://api.smith.langchain.com/redoc#tag/feedback/operation/create_feedback_api_v1_feedback_post
    """
    client = LangsmithClient()
    kwargs = feedback.kwargs or {}
    client.create_feedback(
        run_id=feedback.run_id,
        key=feedback.key,
        score=feedback.score,
        **kwargs,
    )
    return FeedbackResponse()


@router.post("/history")
def history(input: ChatHistoryInput) -> ChatHistory:
    """
    Get chat history.
    """
    # TODO: Hard-coding DEFAULT_AGENT here is wonky
    agent: CompiledStateGraph = get_agent(DEFAULT_AGENT)
    try:
        state_snapshot = agent.get_state(
            config=RunnableConfig(
                configurable={
                    "thread_id": input.thread_id,
                }
            )
        )
        messages: list[AnyMessage] = state_snapshot.values["messages"]
        chat_messages: list[ChatMessage] = [langchain_to_chat_message(m) for m in messages]
        return ChatHistory(messages=chat_messages)
    except Exception as e:
        logging.error(f"An exception occurred: {e}")
        raise HTTPException(status_code=500, detail="Unexpected error")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/performance/metrics")
async def get_performance_metrics():
    """获取性能指标摘要"""
    return {
        "summary": performance_metrics.get_summary(),
        "timestamp": datetime.now().isoformat()
    }


@app.post("/performance/reset")
async def reset_performance_metrics():
    """重置性能指标"""
    performance_metrics.reset()
    return {"message": "Performance metrics reset successfully"}


# curl -s http://localhost:8080/whatsapp/unread_messages
@app.get("/whatsapp/unread_messages")
async def whatsapp_unread_messages():
    """WhatsApp unread messages endpoint."""
    return await whatsapp.get_unread_messages()

# curl -s http://localhost:8080/whatsapp/unread_messages
# [{"contact_name":"f.matheoprod@gmail.com","unread_count":"2","message_preview_container":"default-contact-refreshedf.matheoprod@gmail.com00:17?2","is_muted":false},{"contact_name":"+33 7 75 81 26 36","unread_count":"1","message_preview_container":"+33 7 75 81 26 36昨天Arturmakhmoudov58@gmail.com1","is_muted":false}]# 

# curl -X POST http://localhost:8080/whatsapp/contact_chat_list -H "Content-Type: application/json" -d '{"contact_name": "f.matheoprod@gmail.com"}'
# curl -X POST http://localhost:8080/whatsapp/contact_chat_list -H "Content-Type: application/json" -d '{"contact_name": "+33 7 75 81 26 36"}'
# curl -X POST http://localhost:8080/whatsapp/contact_chat_list -H "Content-Type: application/json" -d '{"contact_name": "AgentsBen"}'
@app.post("/whatsapp/contact_chat_list")
async def whatsapp_contact_chat_list(request: WhatsAppContactInput):
    """WhatsApp contact chat list endpoint."""
    logging.info(f"WhatsApp contact chat list endpoint: {request.contact_name}")
    return await whatsapp.get_contact_chat_list(request.contact_name)

# curl -X POST http://localhost:8080/whatsapp/send_message -H "Content-Type: application/json" -d '{"contact_name": "f.matheoprod@gmail.com", "message": "❤️❤️"}'
# curl -X POST http://localhost:8080/whatsapp/send_message -H "Content-Type: application/json" -d '{"contact_name": "+33 7 75 81 26 36", "message": "let me know when u done the transfer bro, ill top up for u"}'
@app.post("/whatsapp/send_message")
async def whatsapp_send_message(request: WhatsAppMessageInput):
    """WhatsApp send message endpoint."""
    return await whatsapp.send_message_to_contact(request.contact_name, request.message)

# curl -s http://localhost:8080/test
# @app.get("/test")
# async def test():
#     """Main function demonstrating different usage patterns."""
#     logging.info("=== WhatsApp Web Screenshot Demo ===")
#     # Option 1: Use the simple function (runs for 2 minutes as demo)
#     logging.info("Option 1: Using screenshot_whatsapp function")
#     logging.info("This will run for 2 minutes, taking screenshots every 10 seconds")
#     logging.info("Press Ctrl+C to stop early")
    
#     try:
#         await screenshot_whatsapp(interval_seconds=10, duration_minutes=2, headless=True)
#     except KeyboardInterrupt:
#         logging.info("Demo stopped by user")
#     except Exception as e:
#         logging.error(f"Error: {e}", exc_info=True)
    
#     logging.info("\nDemo completed!")



class CustomerServiceResponse(BaseModel):
    """Customer service response model."""
    ai_reply: str
    sentiment: Dict[str, Any]
    categories: List[str]
    timestamp: str
    result: Any


# curl -X POST http://localhost:8080/customer-service/test \
#     -H "Content-Type: application/json" \
#     -d '{"message": "where is my order?", "userToken": "3449ab69-8813-4db5-836c-3b0f047626e3"}'

class CustomerServiceInput(BaseModel):
    """Customer service input model."""
    message: str
    timestamp: Optional[str] = None
    contact_name: Optional[str] = None
    userToken: Optional[str] = None
    thread_id: Optional[str] = None


@app.post("/customer-service/test", response_model=CustomerServiceResponse)
async def test_customer_service(request: CustomerServiceInput):
    """Test endpoint for customer service workflow.
    
    Example curl:
    ```bash
    curl -X POST http://localhost:8080/customer-service/test \
        -H "Content-Type: application/json" \
        -d '{"message": "How do I place an order?"}'
    ```
    """
    if not request.userToken:
        raise HTTPException(status_code=400, detail="userToken is required")
    
    try:
        # Get customer service agent
        agent = get_agent("customer-service")
        
        # Prepare timestamp
        timestamp = request.timestamp or datetime.now().strftime("%H:%M, %Y-%m-%d")
        
        # Format message with timestamp
        message = f"[{timestamp}] {request.message}"
        if request.contact_name:
            message = f"From {request.contact_name}: {message}"
            
        # Create message state
        state = {
            "messages": [HumanMessage(content=message)]
        }
        
        # Run agent
        logging.info(f"Customer service get messages: {state['messages']}")
        
        thread_id = request.thread_id or str(uuid4())
        
        thread_id = str(uuid4())
        
        result = await agent.ainvoke(
            state,
            config={
                "configurable": {
                    "model": settings.DEFAULT_MODEL,
                    "thread_id": thread_id,
                    "userToken": request.userToken
                }
            }
        )
        
        logging.info(f"Customer service agent result: {result}")
        
        # Extract results
        ai_reply = result["messages"][-1].content if result.get("messages") else ""
        sentiment = result.get("sentiment", {"sentiment": "neutral", "confidence": 0.0})
        categories = result.get("categories", {}).get("categories", ["general"])
        
        return CustomerServiceResponse(
            ai_reply=ai_reply,
            sentiment=sentiment,
            categories=categories,
            timestamp=datetime.now().isoformat(),
            result=result,
            thread_id=thread_id,
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# curl -X POST http://localhost:8080/customer-service/user-info \
#     -H "Content-Type: application/json" \
#     -d '{"message": "where is my order?", "userToken": "3449ab69-8813-4db5-836c-3b0f047626e3"}'

@app.post("/customer-service/user-info")
async def get_user_info_endpoint(request: CustomerServiceInput):
    """Get user info endpoint."""
    logging.info(f"Get user info endpoint: {request}")
    
    if not request.userToken:
        raise HTTPException(status_code=400, detail="userToken is required")
    
    return await get_user_summary(request.userToken)

# curl -X POST http://localhost:8080/customer-service/get-product-info \
#     -H "Content-Type: application/json" \
#     -d '{"product_id": "655280629872", "platform": "TAOBAO"}'

class GetProductInfoInput(BaseModel):
    """Get product info input model."""
    product_id: str
    platform: str


@app.post("/customer-service/get-product-info")
async def get_product_info(request: GetProductInfoInput):
    """Get product info endpoint."""
    logging.info(f"Get product info endpoint: {request}")
    return await api_taobao.get_product_info(request.product_id, request.platform)

app.include_router(router)
