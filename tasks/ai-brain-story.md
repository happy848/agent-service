# AI Brain 大脑需求文档

## 项目概述
基于当前Agent Service项目架构，实现一个完整的AI任务管理系统作为整个代理采购网站的"AI大脑"，负责任务的生成、分发、执行、监控和归档。

## 技术架构
- **FastAPI**: RESTful API服务层，提供任务管理接口
- **Celery**: 分布式任务队列，负责异步任务执行
- **Redis**: 消息代理和缓存存储，支持任务状态持久化
- **Celery Beat**: 定时任务调度器
- **Redis Pub/Sub**: 实时事件通知系统

## 核心模块分割

### 1. Brain 模块 (核心控制中心)
**模块路径**: `src/brain/`
**职责**: 作为系统的核心控制中心，负责任务的整体调度和管理

**核心功能**:
- 接受用户输入并理解任务意图
- 根据任务类型生成相应的执行计划
- 将任务分发到任务队列
- 监控任务执行状态
- 提供任务查询、取消、暂停、恢复、重试等操作接口
- 管理定时任务的创建和调度
- 任务优先级管理和资源分配

**待实现功能**:
- BrainController: 核心控制器，处理所有外部请求
- TaskPlanner: 任务规划器，将复杂任务拆分为子任务
- TaskScheduler: 任务调度器，决定任务的执行顺序
- TaskMonitor: 任务监控器，实时监控任务状态
- TaskOrchestrator: 任务编排器，管理任务间的依赖关系

### 2. Task 模块 (任务管理)
**模块路径**: `src/task/`
**职责**: 负责任务的生命周期管理，包括创建、存储、更新、查询和归档

**核心功能**:
- 任务模型定义和验证
- 任务状态管理(待执行、执行中、已完成、失败、取消、暂停)
- 任务元数据管理(优先级、超时时间、重试次数等)
- 任务层级关系管理(父子任务、依赖任务)
- 任务执行结果记录
- 任务历史记录和归档

**待实现功能**:
- TaskModel: 任务数据模型定义
- TaskRepository: 任务数据访问层
- TaskService: 任务业务逻辑服务
- TaskValidator: 任务数据验证器
- TaskArchiver: 任务归档管理器

**任务类型定义**:
- CUSTOMER_REPLY: 客户回复任务
- MESSAGE_PUSH: 消息推送任务
- ORDER_PROCESS: 订单处理任务
- INVENTORY_CHECK: 库存检查任务
- PRICE_INQUIRY: 价格询价任务
- LOGISTICS_TRACK: 物流跟踪任务
- SCHEDULED_REMINDER: 定时提醒任务

### 3. Scheduler 模块 (定时调度)
**模块路径**: `src/scheduler/`
**职责**: 负责定时任务的调度和管理

**核心功能**:
- 定时任务的创建和管理
- 基于时间的任务触发
- 周期性任务的调度
- 任务调度策略管理
- 调度失败处理和重试机制

**待实现功能**:
- SchedulerEngine: 调度引擎，基于Celery Beat
- ScheduleManager: 调度管理器，管理调度规则
- CronTaskHandler: 定时任务处理器
- ScheduleValidator: 调度规则验证器
- ScheduleMonitor: 调度监控器

### 4. Consumer 模块 (任务消费)
**模块路径**: `src/consumer/`
**职责**: 消费任务队列，执行具体的任务逻辑

**核心功能**:
- 从任务队列中消费任务
- 调用相应的Agent或工具执行任务
- 处理任务执行结果
- 任务失败重试机制
- 任务执行状态更新

**待实现功能**:
- BaseConsumer: 消费者基类
- CustomerReplyConsumer: 客户回复任务消费者
- MessagePushConsumer: 消息推送任务消费者
- OrderProcessConsumer: 订单处理任务消费者
- InventoryCheckConsumer: 库存检查任务消费者
- PriceInquiryConsumer: 价格询价任务消费者
- LogisticsTrackConsumer: 物流跟踪任务消费者
- ConsumerManager: 消费者管理器

### 5. Tools 模块 (工具集成)
**模块路径**: `src/tools/`
**职责**: 提供各种外部工具和服务的集成接口

**核心功能**:
- 浏览器自动化工具(基于Playwright)
- 数据库操作工具
- 外部API调用工具
- 文件处理工具
- 通讯工具(WhatsApp、Telegram、邮件等)

**待实现功能**:
- BrowserTool: 浏览器自动化工具
- DatabaseTool: 数据库操作工具
- ApiTool: API调用工具
- FileTool: 文件处理工具
- CommunicationTool: 通讯工具集合
- ToolManager: 工具管理器
- ToolRegistry: 工具注册表

### 6. Memory 模块 (记忆管理)
**模块路径**: `src/memory/`
**职责**: 提供各种记忆存储和管理功能

**核心功能**:
- 任务执行记忆存储
- 对话上下文记忆
- 业务数据缓存
- 学习记忆积累
- 记忆检索和清理

**待实现功能**:
- TaskStorage: 任务数据存储(基于Redis)
- ConversationMemory: 对话记忆管理
- BusinessCache: 业务数据缓存
- LearningMemory: 学习记忆存储
- MemoryManager: 记忆管理器
- MemoryCleanup: 记忆清理服务

### 7. Agents 模块 (AI智能体)
**模块路径**: `src/agents/`
**职责**: 提供各种AI智能体，执行具体的智能任务

**核心功能**:
- 现有的chatbot智能体集成
- 浏览器操作智能体
- 数据库查询智能体
- API调用智能体
- 智能体协调和管理

**待实现功能**:
- 复用现有的chatbot智能体
- BrowserAgent: 浏览器操作智能体
- DatabaseAgent: 数据库查询智能体
- ApiAgent: API调用智能体
- CoordinatorAgent: 智能体协调器
- AgentManager: 智能体管理器

### 8. Service 模块 (服务层)
**模块路径**: `src/service/`
**职责**: 提供各种服务的启动、配置和管理

**核心功能**:
- 扩展现有FastAPI服务，添加任务管理API
- Celery应用配置和管理
- Redis连接和配置
- 服务生命周期管理
- 健康检查和监控

**待实现功能**:
- 扩展现有的service.py，添加任务管理路由
- CeleryApp: Celery应用配置
- RedisManager: Redis连接管理
- ServiceManager: 服务管理器
- HealthChecker: 服务健康检查
- MonitoringService: 监控服务

### 9. Schema 模块 (数据模型)
**模块路径**: `src/schema/`
**职责**: 定义所有数据模型和协议

**核心功能**:
- 扩展现有schema，添加任务相关模型
- 数据验证和序列化
- API请求/响应模型
- 数据库模型定义

**待实现功能**:
- 扩展现有的schema，添加task_models.py
- TaskModel: 任务数据模型
- TaskStatus, TaskType, TaskPriority等枚举
- API请求响应模型
- 数据验证规则

### 10. Core 模块 (核心配置)
**模块路径**: `src/core/`
**职责**: 提供系统核心配置和设置

**核心功能**:
- 扩展现有配置，添加Redis、Celery等配置
- 环境变量管理
- 系统参数配置
- 配置验证和加载

**待实现功能**:
- 扩展现有的settings.py，添加任务管理相关配置
- Redis配置
- Celery配置
- 外部工具API配置
- 任务相关参数配置

### 11. Mocks 模块 (模拟数据)
**模块路径**: `src/mocks/`
**职责**: 提供测试和开发用的模拟数据

**核心功能**:
- 任务模拟数据
- 外部API模拟响应
- 测试场景数据
- 性能测试数据

**待实现功能**:
- TaskMockData: 任务模拟数据
- ApiMockData: API模拟响应
- TestScenarios: 测试场景数据
- PerformanceTestData: 性能测试数据

## 实现优先级

### P0 (核心功能) - 第1周
1. **Schema模块** - 任务数据模型定义
2. **Core模块** - 扩展系统配置
3. **Memory模块** - 基于Redis的任务存储
4. **Task模块** - 任务管理核心逻辑
5. **Service模块** - 扩展FastAPI服务，添加基础API

### P1 (重要功能) - 第2周
1. **Consumer模块** - 基础任务消费者实现
2. **Brain模块** - 核心控制和调度逻辑
3. **Scheduler模块** - 定时任务调度
4. **Tools模块** - 基础工具集成
5. **Service模块** - 完善Celery和事件系统

### P2 (增强功能) - 第3周
1. **Agents模块** - 扩展AI智能体
2. **Consumer模块** - 完善所有任务消费者
3. **Tools模块** - 完善工具集成
4. **Memory模块** - 高级记忆管理
5. **Mocks模块** - 测试数据和部署配置

## 核心特性

### 1. 任务生命周期管理
- 任务创建、提交、执行、完成、失败、重试的完整流程
- 任务状态实时更新和监控
- 任务优先级和资源分配

### 2. 分布式任务执行
- 基于Celery的分布式任务队列
- 多Worker实例支持
- 任务类型分队列处理

### 3. 智能任务调度
- 基于优先级的任务调度
- 任务依赖关系管理
- 定时任务和周期任务支持

### 4. 实时监控和事件
- 基于Redis Pub/Sub的实时事件通知
- WebSocket实时任务状态推送
- 任务执行统计和监控

### 5. 高可用性设计
- Redis持久化保证数据安全
- 任务失败自动重试机制
- 服务健康检查和故障恢复

### 6. 扩展性设计
- 模块化架构，易于扩展
- 插件化任务处理器
- 配置驱动的系统行为

## 技术集成要点

### 1. 与现有系统集成
- 复用现有Agent系统
- 扩展现有FastAPI服务
- 遵循现有Schema规范
- 兼容现有浏览器自动化功能

### 2. 外部工具集成
- WhatsApp、Telegram、邮件等通讯工具
- 订单处理、库存管理、价格查询等业务API
- 物流跟踪、支付处理等第三方服务

### 3. 数据存储策略
- Redis作为主要数据存储(任务状态、缓存)
- 现有数据库存储业务数据
- 文件存储任务相关附件

### 4. 监控和运维
- Celery Flower监控界面
- 结构化日志记录
- 性能指标收集
- 错误追踪和告警

## 交付标准

### 1. 代码质量
- 遵循项目编码规范
- 完整的类型注解
- 充分的单元测试
- 清晰的文档注释

### 2. 功能完整性
- 所有模块按需求实现
- API接口完整可用
- 错误处理机制完善
- 性能满足要求

### 3. 部署就绪
- Docker容器化部署
- 环境配置管理
- 服务启动脚本
- 监控和日志配置

### 4. 文档完整
- API文档(自动生成)
- 部署运维文档
- 使用说明文档
- 故障排查指南

这个AI大脑系统将作为整个代理采购网站的核心调度中心，统一管理和协调各种任务的执行，提供强大的任务管理和调度能力。