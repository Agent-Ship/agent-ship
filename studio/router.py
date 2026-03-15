"""
Debug API Router for the Debug UI.

Provides endpoints for:
- Listing available agents and their schemas
- Streaming chat responses
- Feedback tracking
- Session management
- Log capture for debugging
"""

import asyncio
import io
import json
import logging
import os
import uuid
import httpx
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from src.agent_framework.registry import list_agents, get_agent_instance, get_agent_class
from src.service.models.base_models import AgentChatRequest, FeatureMap

logger = logging.getLogger(__name__)
router = APIRouter()


# ============== Log Capture ==============

class LogCaptureHandler(logging.Handler):
    """Captures logs during agent execution for debug display."""
    
    def __init__(self):
        super().__init__()
        self.logs: List[Dict[str, Any]] = []
        self.setFormatter(logging.Formatter('%(asctime)s | %(name)s | %(message)s', datefmt='%H:%M:%S'))
    
    def emit(self, record: logging.LogRecord):
        try:
            # Categorize logs
            category = "info"
            msg = record.getMessage()
            
            # Detect tool calls
            if any(kw in msg.lower() for kw in ['tool', 'function', 'calling', 'executing']):
                category = "tool"
            elif any(kw in msg.lower() for kw in ['llm', 'model', 'gemini', 'gpt', 'claude', 'response']):
                category = "llm"
            elif record.levelno >= logging.WARNING:
                category = "warning"
            elif record.levelno >= logging.ERROR:
                category = "error"
            
            self.logs.append({
                "timestamp": self.format(record).split(' | ')[0],
                "logger": record.name,
                "level": record.levelname,
                "message": msg,
                "category": category
            })
        except Exception:
            pass
    
    def get_logs(self) -> List[Dict[str, Any]]:
        return self.logs
    
    def clear(self):
        self.logs = []


@contextmanager
def capture_logs():
    """Context manager to capture logs during agent execution."""
    handler = LogCaptureHandler()
    handler.setLevel(logging.DEBUG)
    
    # Add handler to root logger and relevant agent loggers
    root_logger = logging.getLogger()
    original_level = root_logger.level
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(handler)
    
    # Also capture google.adk logs for tool calls
    adk_logger = logging.getLogger('google.adk')
    adk_logger.addHandler(handler)
    
    try:
        yield handler
    finally:
        root_logger.removeHandler(handler)
        root_logger.setLevel(original_level)
        adk_logger.removeHandler(handler)


# ============== Models ==============

class AgentInfo(BaseModel):
    """Basic agent information."""
    name: str
    description: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None


class ChatMessage(BaseModel):
    """A chat message."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: str
    feedback: Optional[str] = None  # "up", "down", or None


class DebugChatRequest(BaseModel):
    """Request for debug chat."""
    agent_name: str
    message: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    features: Optional[Dict[str, Any]] = None


class FeedbackRequest(BaseModel):
    """Request to save feedback."""
    session_id: str
    message_index: int
    message_id: Optional[str] = None  # Optional message_id (for backend API)
    feedback: str  # "up" or "down"
    agent_name: str
    user_message: str
    assistant_message: str


class SessionInfo(BaseModel):
    """Session information."""
    session_id: str
    agent_name: str
    created_at: str
    message_count: int


# ============== In-Memory Storage ==============
# For tracking feedback and sessions in debug mode

_feedback_store: List[Dict[str, Any]] = []
_sessions: Dict[str, SessionInfo] = {}


# ============== Helper Functions ==============

def _find_schema_classes_from_module(agent_class) -> tuple:
    """Find Input/Output schema classes from the agent's module."""
    import inspect
    
    module = inspect.getmodule(agent_class)
    input_schema = None
    output_schema = None
    
    if module:
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if hasattr(obj, 'model_json_schema'):
                if name.endswith("Input"):
                    input_schema = obj
                elif name.endswith("Output"):
                    output_schema = obj
    
    return input_schema, output_schema


def _get_agent_schema(agent_name: str) -> Dict[str, Any]:
    """Get input/output schema for an agent without instantiating it."""
    try:
        agent_class = get_agent_class(agent_name)
        input_schema_class, output_schema_class = _find_schema_classes_from_module(agent_class)
        
        result = {"input": {}, "output": {}}
        
        if input_schema_class:
            schema = input_schema_class.model_json_schema()
            result["input"] = {
                "title": schema.get("title", "Input"),
                "properties": schema.get("properties", {}),
                "required": schema.get("required", [])
            }
        
        if output_schema_class:
            schema = output_schema_class.model_json_schema()
            result["output"] = {
                "title": schema.get("title", "Output"),
                "properties": schema.get("properties", {}),
                "required": schema.get("required", [])
            }
        
        return result
    except Exception as e:
        logger.warning(f"Could not get schema for {agent_name}: {e}")
        return {"input": {}, "output": {}}


# ============== Endpoints ==============

@router.get("/agents", response_model=List[AgentInfo])
async def get_agents():
    """Get list of all available agents with their schemas."""
    try:
        agent_names = list_agents()
        agents = []
        
        for name in agent_names:
            schema = _get_agent_schema(name)
            agents.append(AgentInfo(
                name=name,
                description=f"Agent: {name}",
                input_schema=schema.get("input"),
                output_schema=schema.get("output")
            ))
        
        return agents
    except Exception as e:
        logger.exception("Failed to list agents")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agents/{agent_name}/schema")
async def get_agent_schema(agent_name: str):
    """Get detailed schema for a specific agent."""
    try:
        schema = _get_agent_schema(agent_name)
        return schema
    except Exception as e:
        logger.exception(f"Failed to get schema for {agent_name}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agents/{agent_name}/config")
async def get_agent_config(agent_name: str):
    """Get engine/model configuration for a specific agent (used by AgentShip Studio)."""
    try:
        agent = get_agent_instance(agent_name)
        config = agent.agent_config
        memory_backend = "in_memory"
        if hasattr(config, "memory") and config.memory and hasattr(config.memory, "backend"):
            memory_backend = config.memory.backend
        model = getattr(config, "model", None) or getattr(config, "llm_model", None)
        model_str = model.value if hasattr(model, "value") else (str(model) if model else "unknown")
        provider = getattr(config, "model_provider", None) or getattr(config, "llm_provider_name", None)
        provider_str = (provider.name.value if hasattr(provider, "name") else str(provider)) if provider else "unknown"
        return {
            "engine": getattr(config, "execution_engine", "adk") or "adk",
            "model": model_str,
            "provider": provider_str,
            "memory_backend": memory_backend,
            "streaming_mode": getattr(config, "streaming_mode", "event_based") or "event_based",
        }
    except Exception as e:
        logger.warning(f"Failed to get config for {agent_name}: {e}")
        return {"engine": "unknown", "model": "unknown", "provider": "unknown", "memory_backend": "unknown", "streaming_mode": "event_based"}


@router.post("/chat")
async def debug_chat(request: DebugChatRequest):
    """
    Non-streaming chat endpoint for debug UI.
    Returns the full response at once with captured logs.
    """
    captured_logs = []
    
    try:
        # Generate IDs if not provided
        session_id = request.session_id or f"debug_{uuid.uuid4().hex[:12]}"
        user_id = request.user_id or f"debug_user_{uuid.uuid4().hex[:8]}"
        
        # Build query from features (form data) or message
        if request.features:
            query = dict(request.features)
            query['session_id'] = session_id
            query['user_id'] = user_id
        else:
            query = {"text": request.message}
        
        # Create chat request
        chat_request = AgentChatRequest(
            agent_name=request.agent_name,
            user_id=user_id,
            session_id=session_id,
            sender="USER",
            query=query,
            features=[]
        )
        
        logger.info(f"Debug chat request: agent={request.agent_name}")
        
        # Capture logs during agent execution
        with capture_logs() as log_handler:
            # Get agent and chat
            agent = get_agent_instance(request.agent_name)
            result = await agent.chat(chat_request)
            captured_logs = log_handler.get_logs()
        
        # Extract response text - handle various response formats
        response_text = ""
        if hasattr(result, 'agent_response'):
            resp = result.agent_response
            
            # Handle Pydantic models
            if hasattr(resp, 'model_dump'):
                resp = resp.model_dump()
            elif hasattr(resp, 'dict'):
                resp = resp.dict()
            
            if isinstance(resp, dict):
                response_text = (
                    resp.get('response') or 
                    resp.get('text') or 
                    resp.get('answer') or 
                    resp.get('translated_text') or
                    resp.get('summary') or
                    resp.get('message') or
                    resp.get('flight_plan') or
                    resp.get('hotel_plan') or
                    json.dumps(resp, indent=2)
                )
            elif isinstance(resp, str):
                response_text = resp
            else:
                try:
                    if hasattr(resp, '__dict__'):
                        resp_dict = {k: v for k, v in resp.__dict__.items() if not k.startswith('_')}
                        response_text = json.dumps(resp_dict, indent=2, default=str)
                    else:
                        response_text = str(resp)
                except:
                    response_text = str(resp)
        
        # Track session
        if session_id not in _sessions:
            _sessions[session_id] = SessionInfo(
                session_id=session_id,
                agent_name=request.agent_name,
                created_at=datetime.now().isoformat(),
                message_count=0
            )
        _sessions[session_id].message_count += 1
        
        # Filter logs to show only interesting ones
        filtered_logs = [
            log for log in captured_logs 
            if log['category'] in ['tool', 'llm', 'warning', 'error'] 
            or 'agent' in log['logger'].lower()
            or 'adk' in log['logger'].lower()
        ]
        
        return {
            "session_id": session_id,
            "user_id": user_id,
            "response": response_text,
            "success": True,
            "logs": filtered_logs[-50:]  # Last 50 relevant logs
        }
        
    except Exception as e:
        logger.exception("Debug chat failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
async def debug_chat_stream(request: DebugChatRequest):
    """
    Streaming chat endpoint using Server-Sent Events.
    
    Streams real events as they happen including:
    - session: Session info (sent first)
    - thinking: Agent is processing
    - tool_call: Agent is calling a tool (shows function name + args)
    - tool_result: Tool returned a result
    - content: Agent's text response
    - done: Stream complete
    - error: An error occurred
    """
    logger.info(f"🚀 Stream request received for agent: {request.agent_name}")
    
    async def event_generator():
        stream_completed = False  # Initialize before try block
        try:
            session_id = request.session_id or f"debug_{uuid.uuid4().hex[:12]}"
            user_id = request.user_id or f"debug_user_{uuid.uuid4().hex[:8]}"
            
            logger.info(f"📡 Starting stream for session: {session_id}")
            
            # Send session info first
            yield {
                "event": "session",
                "data": json.dumps({"session_id": session_id, "user_id": user_id})
            }
            
            # Build features
            features = []
            if request.features:
                for key, value in request.features.items():
                    features.append(FeatureMap(feature_name=key, feature_value=value))
            
            # Parse the query - prefer features (form data) over message (JSON string)
            # The debug UI sends form data as features, and message as JSON.stringify(formData)
            query = {}
            
            # First, use features directly (this is the form data from the UI: source, destination, etc.)
            if request.features:
                query = dict(request.features)
            
            # If no features but message is provided, try to parse it as JSON
            if not query and request.message:
                try:
                    parsed_message = json.loads(request.message)
                    if isinstance(parsed_message, dict):
                        query = parsed_message
                    else:
                        query = {"text": request.message}
                except (json.JSONDecodeError, TypeError):
                    query = {"text": request.message}
            
            # Create request
            chat_request = AgentChatRequest(
                agent_name=request.agent_name,
                user_id=user_id,
                session_id=session_id,
                sender="USER",
                query=query,
                features=features
            )
            
            # Get agent and use streaming chat
            agent = get_agent_instance(request.agent_name)
            logger.info(f"🤖 Got agent instance, starting chat_stream...")
            
            # Stream real events from the agent
            try:
                async for event in agent.chat_stream(chat_request):
                    event_type = event.get("type", "message")
                    
                    # Log content events with their actual content
                    if event_type == "content":
                        content_text = event.get("text", "") or event.get("content", "")
                        logger.info(f"📨 Yielding content event: type={event_type}, text_length={len(str(content_text))}, preview={str(content_text)[:100] if content_text else 'EMPTY'}")
                    else:
                        logger.info(f"📨 Yielding event: {event_type}")
                    
                    # Skip duplicate "done" events (agent already sends one)
                    if event_type == "done":
                        stream_completed = True
                    
                    # Log the actual data being sent for content events
                    if event_type == "content":
                        event_data_str = json.dumps(event)
                        logger.info(f"📤 Sending content event via SSE: event={event_type}, data_length={len(event_data_str)}, data_preview={event_data_str[:200]}")
                    
                    yield {
                        "event": event_type,
                        "data": json.dumps(event)
                    }
                
                # Track session after streaming completes successfully
                if session_id not in _sessions:
                    _sessions[session_id] = SessionInfo(
                        session_id=session_id,
                        agent_name=request.agent_name,
                        created_at=datetime.now().isoformat(),
                        message_count=0
                    )
                _sessions[session_id].message_count += 1
                stream_completed = True
                
            except (GeneratorExit, asyncio.CancelledError):
                logger.info("Client disconnected, stream cancelled for %s", request.agent_name)
                stream_completed = True
                return
            except Exception as stream_error:
                logger.exception("Error during agent chat_stream")
                yield {
                    "event": "error",
                    "data": json.dumps({"type": "error", "message": str(stream_error)})
                }

        except Exception as e:
            logger.exception("Streaming chat failed")
            yield {
                "event": "error",
                "data": json.dumps({"type": "error", "message": str(e)})
            }
        finally:
            # Always yield a done event to properly close the stream (if agent didn't already)
            if not stream_completed:
                yield {
                    "event": "done",
                    "data": json.dumps({"type": "done"})
                }
    
    return EventSourceResponse(event_generator())


@router.post("/feedback")
async def save_feedback(request: FeedbackRequest):
    """
    Save feedback for a message.
    
    This endpoint proxies feedback to the backend API for persistent storage.
    If backend is not available, falls back to in-memory storage for debug purposes.
    """
    try:
        # Generate message_id if not provided (for debug UI)
        message_id = request.message_id or f"debug_msg_{uuid.uuid4().hex[:12]}"
        
        # Map feedback values: "up" -> "helpful", "down" -> "not_helpful"
        feedback_value = "helpful" if request.feedback == "up" else "not_helpful"
        
        # Send feedback to AI ecosystem feedback endpoint (same service)
        # This stores feedback in the same database as agent sessions
        try:
            # Directly use the database model instead of HTTP call (same service)
            from src.service.models.ai_feedback import (
                FeedbackRequest as FeedbackRequestModel,
                FeedbackType,
                get_db,
                AIFeedback,
                engine
            )
            from sqlalchemy.orm import Session
            
            if not engine:
                raise ValueError("Database not configured for feedback storage.")

            # Create feedback request model
            feedback_request_model = FeedbackRequestModel(
                message_id=message_id,
                session_id=request.session_id,
                user_id="debug_user",  # Use a placeholder user_id for debug UI
                agent_name=request.agent_name,
                input_message=request.user_message,
                output_message=request.assistant_message,
                feedback=feedback_value
            )
            
            # Get database session and save feedback
            db = next(get_db())
            try:
                # Check if feedback already exists
                existing_feedback = db.query(AIFeedback).filter(
                    AIFeedback.message_id == message_id
                ).first()
                
                if existing_feedback:
                    existing_feedback.feedback = FeedbackType(feedback_value)
                    existing_feedback.input_message = request.user_message
                    existing_feedback.output_message = request.assistant_message
                    existing_feedback.agent_name = request.agent_name
                    db.commit()
                    feedback_id = str(existing_feedback.feedback_id)
                else:
                    # Create new feedback
                    feedback = AIFeedback(
                        message_id=message_id,
                        session_id=request.session_id,
                        user_id="debug_user",
                        agent_name=request.agent_name,
                        input_message=request.user_message,
                        output_message=request.assistant_message,
                        feedback=FeedbackType(feedback_value)
                    )
                    db.add(feedback)
                    db.commit()
                    db.refresh(feedback)
                    feedback_id = str(feedback.feedback_id)
                
                logger.info(f"Feedback saved to AI ecosystem database: {feedback_value} for {request.agent_name}")
                return {
                    "success": True,
                    "id": feedback_id,
                    "saved_to_database": True
                }
            finally:
                db.close()
                
        except Exception as db_error:
            logger.warning(f"Failed to save feedback to database: {db_error}. Using in-memory storage.")
            # Fall through to in-memory storage
        
        # Fallback to in-memory storage (for debug purposes if database fails)
        feedback_entry = {
            "id": message_id,
            "timestamp": datetime.now().isoformat(),
            "session_id": request.session_id,
            "message_index": request.message_index,
            "message_id": message_id,
            "feedback": request.feedback,
            "agent_name": request.agent_name,
            "user_message": request.user_message,
            "assistant_message": request.assistant_message,
            "saved_to_database": False
        }
        
        _feedback_store.append(feedback_entry)
        logger.info(f"Feedback saved to in-memory store: {request.feedback} for {request.agent_name}")
        
        return {"success": True, "id": feedback_entry["id"], "saved_to_database": False}
        
    except Exception as e:
        logger.exception("Failed to save feedback")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/feedback")
async def get_feedback():
    """Get all feedback (for debugging/export)."""
    return _feedback_store


@router.get("/sessions")
async def get_sessions():
    """Get all debug sessions."""
    return list(_sessions.values())


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a debug session."""
    if session_id in _sessions:
        del _sessions[session_id]
        return {"success": True}
    raise HTTPException(status_code=404, detail="Session not found")


@router.post("/sessions")
async def create_session(agent_name: str):
    """Create a new debug session."""
    session_id = f"debug_{uuid.uuid4().hex[:12]}"
    _sessions[session_id] = SessionInfo(
        session_id=session_id,
        agent_name=agent_name,
        created_at=datetime.now().isoformat(),
        message_count=0
    )
    return _sessions[session_id]


