from langgraph.graph import StateGraph, END

# 定义状态
class State(dict):
    pass

# 定义节点
def step1(state: State):
    print("Step 1")
    return {"msg": "hello"}

def step2(state: State):
    print("Step 2")
    return {"msg": state["msg"] + " world"}

# 定义图
graph = StateGraph(State)
graph.add_node("step1", step1)
graph.add_node("step2", step2)
graph.set_entry_point("step1")
graph.add_edge("step1", "step2")
graph.add_edge("step2", END)

app = graph.compile()
