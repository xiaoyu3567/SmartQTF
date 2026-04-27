# 📄 项目文档：Quant Trading Framework v1

## 1. 设计目标

* 模块完全解耦（禁止跨层直接调用）
* 所有层通过 **接口 + schema** 通信
* 支持：

  * 多数据源
  * 多策略
  * 多交易所
* 可测试（每层独立 unit test）
* 可模拟（mock exchange）

---

## 2. 项目目录结构（必须严格遵守）

```
quant/
│
├── data/                  # 数据层
│   ├── providers/        # 数据来源（binance / okx / csv）
│   ├── schemas/          # 数据结构定义
│   ├── adapters/         # 数据标准化
│   └── tests/
│
├── features/             # 特征层（alpha）
│   ├── indicators/
│   ├── transformers/
│   └── tests/
│
├── strategies/           # 策略层
│   ├── base/
│   ├── implementations/
│   └── tests/
│
├── execution/            # 执行层
│   ├── engine/
│   ├── brokers/
│   ├── state_machine/
│   └── tests/
│
├── risk/                 # 风控层
│   ├── rules/
│   └── tests/
│
├── backtest/             # 回测系统
│   ├── engine/
│   └── tests/
│
├── config/               # 配置
│   ├── default.yaml
│
├── scripts/              # 运行脚本
│   ├── run_backtest.py
│   ├── run_live.py
│
└── tests/                # 集成测试
```

---

## 3. 各层接口定义（核心约束）

---

## 3.1 数据层（data）

### 目标

统一所有数据输入格式

### 标准数据结构（必须实现）

```python
class Kline:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
```

```python
class Trade:
    timestamp: int
    price: float
    size: float
    side: str  # buy/sell
```

### 接口定义

```python
class DataProvider:
    def get_klines(self, symbol: str, timeframe: str):
        pass

    def get_trades(self, symbol: str):
        pass
```

### 约束

* 不允许返回未标准化数据
* 时间必须统一（UTC）

---

## 3.2 特征层（features）

### 目标

生成 alpha 信号

### 接口

```python
class Feature:
    def compute(self, data):
        pass
```

### 示例

```python
class OrderFlowImbalance(Feature):
    def compute(self, trades):
        return buy_volume - sell_volume
```

### 约束

* 禁止访问策略层
* 禁止下单逻辑

---

## 3.3 策略层（strategies）

### 目标

把 feature → signal

### 接口

```python
class Strategy:
    def generate_signal(self, features):
        """
        return:
        {
            "action": "buy/sell/hold",
            "confidence": float
        }
        """
```

### 约束

* 不允许直接访问交易所
* 不处理订单

---

## 3.4 执行层（execution）

### 目标

把 signal → order → position

---

## 状态机（必须实现）

```id="exec-state-machine"
IDLE
  ↓
SIGNAL_RECEIVED
  ↓
ORDER_PENDING
  ↓
FILLED / PARTIAL
  ↓
POSITION_OPEN
  ↓
EXIT_SIGNAL
  ↓
ORDER_CLOSE
  ↓
IDLE
```

---

### 接口

```python
class ExecutionEngine:
    def on_signal(self, signal):
        pass

    def on_order_update(self, order):
        pass
```

---

### Broker接口

```python
class Broker:
    def place_order(self, symbol, side, qty, price=None):
        pass
```

---

## 3.5 风控层（risk）

### 接口

```python
class RiskRule:
    def check(self, context) -> bool:
        pass
```

### 示例

* 最大仓位限制
* 最大亏损限制

---

## 3.6 回测层（backtest）

### 接口

```python
class BacktestEngine:
    def run(self, strategy, data):
        pass
```

---

# 4. 插件式设计（核心）

每一层必须支持注册机制：

```python
registry = {}

def register(name, cls):
    registry[name] = cls
```

例如：

```python
register("binance", BinanceDataProvider)
register("orderflow", OrderFlowImbalance)
register("mean_reversion", MeanReversionStrategy)
```

---

# 5. 测试规范（必须实现）

## 每层必须有：

### 1）单元测试

```
pytest data/tests/
pytest features/tests/
```

---

### 2）Mock测试（重点）

例如 execution：

```python
class MockBroker(Broker):
    def place_order(...):
        return {"status": "filled"}
```

---

### 3）集成测试

```
tests/test_full_pipeline.py
```

测试流程：

```
data → features → strategy → execution → result
```

---

# 6. 运行脚本

## 回测

```
python scripts/run_backtest.py --config=config/default.yaml
```

## 实盘

```
python scripts/run_live.py
```

---

# 7. Claude 使用说明（直接给它）

你可以这样喂它：

---

## Prompt 模板

```
你需要实现一个量化交易系统的【数据层】。

要求：
1. 严格按照接口定义
2. 每个模块独立文件
3. 提供 pytest 测试
4. 提供 mock 数据测试
5. 不允许写策略或执行逻辑

输出：
- 完整代码
- 文件结构
- 测试代码
```

---

然后逐层生成：

1. data
2. features
3. strategy
4. execution（最后做）

---

# 8. 验证 checklist（你必须亲自做）

每一层完成后检查：

### 数据层

* 是否时间对齐
* 是否无 future data

### 特征层

* 是否可重复计算
* 是否无未来函数

### 策略层

* 是否 deterministic

### 执行层（重点）

* 是否处理：

  * 部分成交
  * 拒单
  * 延迟


推荐技术栈（不要再纠结）

核心：

Python 3.11+
pydantic（数据 schema）
pytest（测试）
typer（CLI，用于你说的“输入1234测试”）
pandas（数据处理）
pyyaml（配置）

结构原则：

强类型（否则 AI 会乱写）
所有 I/O 都 mockable