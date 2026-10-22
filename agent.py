import asyncio
import os
from typing import TypedDict, Annotated, Sequence

from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage, ToolMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# ---------- بارگذاری متغیرهای محیطی ----------
load_dotenv()

LIARA_API_TOKEN = os.getenv("LIARA_API_TOKEN")
LIARA_AI_API_KEY = os.getenv("LIARA_AI_API_KEY")
LIARA_TEAM_ID = os.getenv("LIARA_TEAM_ID")

if not LIARA_API_TOKEN:
    raise ValueError("LIARA_API_TOKEN (کلید پلتفرم) در .env تنظیم نشده است!")
if not LIARA_AI_API_KEY:
    raise ValueError("LIARA_AI_API_KEY (کلید هوش مصنوعی) در .env تنظیم نشده است!")

# ---------- System Prompt: نقش SRE Agent ----------
SYSTEM_PROMPT = """تو یک SRE Agent هوشمند برای پلتفرم لیارا هستی.

وظایف تو:
1. وقتی کاربر از کندی، خطا (مثل HTTP 500)، یا مشکل عملکردی می‌گه:
   - اول با ابزار liara_get_infrastructure_overview وضعیت CPU/RAM/دیسک را بررسی کن.
   - سپس با liara_manage_app (action=logs) لاگ‌های اپ را بخوان.
   - علت‌های احتمالی را اولویت‌بندی کن (مصرف بالای RAM، خطای برنامه، محدودیت CPU، خطای دیتابیس و...).
   - راه‌حل پیشنهاد بده و اگر نیاز به اقدام اصلاحی بود (مثل restart)، از کاربر تأیید بگیر.
   - بعد از اقدام، نتیجه را بررسی کن و گزارش بده.

2. وقتی کاربر درخواست لیست یا مدیریت اپ می‌ده، از ابزارهای مربوطه استفاده کن.

3. اگر اطلاعات کافی نیست، از کاربر بپرس. اگر عملیاتی از طریق API در دسترس نیست،
   محدودیت را شفاف به کاربر اعلام کن.

4. همیشه به فارسی، شفاف و قدم‌به‌قدم پاسخ بده. پیشنهادهایت را با دلیل ارائه کن.
"""

# ---------- ابزارهای حساس بر اساس (tool, action) ----------
SENSITIVE_ACTIONS = {
    ("liara_manage_app", "restart"),
    ("liara_manage_app", "resize"),
    ("liara_manage_app", "delete"),
    ("liara_manage_app", "create"),
    ("liara_manage_deployment", "deploy"),
    ("liara_manage_deployment", "rollback"),
    ("liara_manage_env_vars", "set"),
    ("liara_manage_env_vars", "delete"),
    ("liara_manage_databases", "create"),
    ("liara_manage_databases", "delete"),
}

# ---------- State ----------
class AgentState(TypedDict):
    messages: Annotated[Sequence, add_messages]

# ---------- Node: تصمیم‌گیری ----------
async def decide_node(state: AgentState, config):
    llm = config["configurable"]["llm_with_tools"]
    response = await llm.ainvoke(state["messages"])
    return {"messages": [response]}

# ---------- Node: اجرای ابزار + HITL ----------
async def tool_node(state: AgentState, config):
    last_msg = state["messages"][-1]
    tool_call = last_msg.tool_calls[0]
    tool_name = tool_call["name"]
    tool_args = tool_call["args"]

    action = str(tool_args.get("action", "")).lower()
    is_sensitive = (tool_name, action) in SENSITIVE_ACTIONS

    if is_sensitive:
        approval = interrupt({
            "type": "approval_request",
            "tool": tool_name,
            "action": action,
            "args": tool_args,
            "reason": f"عملیات «{action}» روی {tool_name} روی سرویس تأثیر می‌گذارد. تأیید می‌کنی؟",
        })
        if not approval.get("approved"):
            return {"messages": [ToolMessage(
                content=f"کاربر عملیات «{action}» را رد کرد.",
                tool_call_id=tool_call["id"],
            )]}

    mcp_tools = config["configurable"]["mcp_tools"]
    tool_fn = next((t for t in mcp_tools if t.name == tool_name), None)
    result = await tool_fn.ainvoke(tool_args) if tool_fn else f"ابزار {tool_name} پیدا نشد"
    return {"messages": [ToolMessage(content=str(result), tool_call_id=tool_call["id"])]}

# ---------- شرط ادامه ----------
def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tool"
    return END

# ---------- ساخت گراف ----------
def build_graph():
    g = StateGraph(AgentState)
    g.add_node("decide", decide_node)
    g.add_node("tool", tool_node)
    g.set_entry_point("decide")
    g.add_conditional_edges("decide", should_continue, {"tool": "tool", END: END})
    g.add_edge("tool", "decide")
    memory = MemorySaver()
    return g.compile(checkpointer=memory)

# ---------- Main ----------
async def main():
    mcp_env = {
        **os.environ,
        "LIARA_API_TOKEN": LIARA_API_TOKEN,
        # "LIARA_MCP_CONSOLIDATED": "true",
    }
    if LIARA_TEAM_ID:
        mcp_env["LIARA_TEAM_ID"] = LIARA_TEAM_ID

    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "liara-mcp"],
        env=mcp_env,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = await load_mcp_tools(session)
            print("ابزارهای موجود:", [t.name for t in mcp_tools])
            if LIARA_TEAM_ID:
                print(f"حالت تیم فعال است: {LIARA_TEAM_ID}")

            llm = ChatOpenAI(
                model="openai/gpt-4o-mini",
                api_key=LIARA_AI_API_KEY,
                base_url="https://ai.liara.ir/api/6ada21b27508e6ca2f0acf7f/v1",
                temperature=0,
            )
            llm_with_tools = llm.bind_tools(mcp_tools)

            graph = build_graph()
            config = {
                "configurable": {
                    "llm_with_tools": llm_with_tools,
                    "mcp_tools": mcp_tools,
                    "thread_id": "sre-session-1",
                }
            }

            user_input = input("درخواستت چیه؟ ")

            # system prompt + درخواست کاربر
            initial_messages = [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=user_input),
            ]

            result = await graph.ainvoke(
                {"messages": initial_messages},
                config,
            )

            # حلقه‌ی تأیید (HITL)
            while "__interrupt__" in result:
                print("\n--- تأیید لازم ---")
                print(result["__interrupt__"])
                answer = input("تأیید می‌کنی؟ (y/n): ")
                result = await graph.ainvoke(
                    Command(resume={"approved": answer.lower() == "y"}),
                    config,
                )

            print("\nنتیجه:", result["messages"][-1].content)

if __name__ == "__main__":
    asyncio.run(main())