# WhatsApp客户端HTML结构修正文档

## 概述

基于提供的WhatsApp Web实际HTML结构，对`send_message_to_contact`函数及其相关辅助函数进行了全面修正，以确保与真实的DOM结构完全匹配。

## 修正的关键函数

### 1. `_search_and_select_contact()`

**主要修正：**
- **搜索激活**：添加了搜索按钮点击功能，支持多种选择器
- **搜索输入框**：使用正确的 `contenteditable="true"` 选择器而非 `input` 元素
- **输入方式**：创建正确的段落元素结构来匹配HTML
- **搜索结果**：基于实际的搜索结果HTML结构进行解析

**关键选择器更新：**
```python
# 搜索激活按钮
'[aria-label="搜索或开始新对话"]'
'[data-icon="search"]'

# 搜索输入框
'[aria-label="搜索输入内容文本框"][contenteditable="true"]'
'.lexical-rich-text-input [contenteditable="true"][role="textbox"]'

# 搜索结果
'[aria-label="搜索结果。"][role="grid"]'
```

### 2. `_get_search_results()`

**主要修正：**
- **结果识别**：跳过分组标题（如"对话"、"共同群组"）
- **选择器优化**：使用HTML中实际的联系人名称选择器
- **结果过滤**：改进匹配逻辑

**关键选择器：**
```python
# 联系人名称
'span[dir="auto"][title]'  # 主要选择器
'span.x1iyjqo2.x6ikm8r.x10wlt62.x1n2onr6.xlyipyv.xuxw1ft.x1rg5ohu._ao3e'
```

### 3. `_clear_search()`

**主要修正：**
- **取消按钮**：使用实际的取消搜索按钮
- **清理方式**：支持多种清理方法

**关键选择器：**
```python
# 取消搜索按钮
'[aria-label="取消搜索"]'
'[data-icon="x-alt"]'
```

### 4. `_find_contact_in_chat_list()`

**主要修正：**
- **聊天列表定位**：使用正确的网格结构选择器
- **联系人识别**：改进名称提取逻辑
- **文本过滤**：排除时间戳和过滤器名称

**关键选择器：**
```python
# 聊天列表
'[aria-label="对话列表"][role="grid"]'

# 联系人名称
'span[dir="auto"][title]'  # 主要选择器
```

### 5. `_clear_chat_filters()`

**主要修正：**
- **过滤器按钮**：使用实际的过滤器ID和状态
- **清理策略**：点击"所有"过滤器来清除其他活跃过滤器

**关键选择器：**
```python
# 过滤器按钮
'#unread-filter[aria-pressed="true"]'    # 未读
'#favorites-filter[aria-pressed="true"]' # 特别关注
'#group-filter[aria-pressed="true"]'     # 群组
'#all-filter'                            # 所有（用于清除）
```

### 6. `_get_current_chat_contact_name()`

**主要修正：**
- **头部选择器**：扩展了联系人名称检测选择器
- **文本过滤**：排除状态信息和时间戳
- **备用策略**：增加多种备用选择器

**关键选择器：**
```python
# 聊天头部联系人名称
'#main header span[dir="auto"][title]'
'#main header [data-testid="conversation-info"] span[dir="auto"]'
'#main header ._ao3e[dir="auto"]'
```

### 7. `_click_chat_contact()` 和 `_click_search_result()`

**主要修正：**
- **点击策略**：改进元素定位和点击逻辑
- **滚动处理**：确保元素可见后再点击
- **等待时间**：调整等待时间以适应实际加载速度

## HTML结构分析要点

### 1. 搜索功能结构
```html
<!-- 搜索输入框 -->
<div aria-label="搜索输入内容文本框" 
     contenteditable="true" 
     role="textbox" 
     data-lexical-editor="true">
  <p class="selectable-text copyable-text x15bjb6t x1n2onr6">
    <span data-lexical-text="true">搜索内容</span>
  </p>
</div>

<!-- 取消搜索按钮 -->
<button aria-label="取消搜索">
  <span data-icon="x-alt"></span>
</button>
```

### 2. 聊天列表结构
```html
<!-- 聊天列表容器 -->
<div aria-label="对话列表" role="grid">
  <!-- 聊天项 -->
  <div role="listitem">
    <div role="button">
      <!-- 联系人名称 -->
      <span dir="auto" title="联系人名称" class="_ao3e">联系人名称</span>
    </div>
  </div>
</div>
```

### 3. 过滤器结构
```html
<!-- 过滤器标签 -->
<button role="tab" 
        aria-pressed="true" 
        id="all-filter" 
        aria-selected="true">
  <div>所有</div>
</button>

<button role="tab" 
        aria-pressed="false" 
        id="unread-filter">
  <div>未读</div>
</button>
```

### 4. 搜索结果结构
```html
<!-- 搜索结果网格 -->
<div aria-label="搜索结果。" role="grid">
  <!-- 分组标题 -->
  <div role="listitem">
    <div class="x9f619 x4f6e3x x1jchvi3">对话</div>
  </div>
  
  <!-- 搜索结果项 -->
  <div role="listitem">
    <div role="button">
      <span dir="auto" title="联系人名称" class="_ao3e">联系人名称</span>
    </div>
  </div>
</div>
```

## 测试建议

使用提供的测试脚本 `src/tools/test_send_message.py` 来验证修正后的功能：

```bash
cd src/tools
python test_send_message.py
```

测试脚本包含：
1. 完整的消息发送测试
2. 搜索功能专项测试
3. 不同场景的测试用例

## 关键改进点

1. **准确性提升**：所有选择器现在基于真实HTML结构
2. **鲁棒性增强**：增加了多种备用选择器和错误处理
3. **性能优化**：改进了元素定位和等待策略
4. **兼容性提升**：支持不同的WhatsApp Web界面变化

## 注意事项

1. **动态内容**：WhatsApp Web的HTML结构可能会随版本更新而变化
2. **网络延迟**：等待时间可能需要根据网络状况调整
3. **语言设置**：选择器中的中文标签可能因界面语言设置而不同

## 使用示例

```python
# 基本使用
result = await client.send_message_to_contact(
    contact_name="联系人名称",
    message="测试消息"
)

# 检查结果
if result["success"]:
    print(f"消息发送成功，方式：{result['method_used']}")
else:
    print(f"发送失败：{result['error']}")
```

所有修正确保函数能够：
1. 正确检测当前聊天联系人
2. 在聊天列表中准确找到目标联系人
3. 使用搜索功能查找不在列表中的联系人
4. 处理各种边缘情况和错误 