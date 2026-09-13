# Spikes

技術可行性驗證，**不是正式程式碼**，不接進主流程。
留著是因為「當初驗過什麼、結果如何」比結論本身更值得保存。

## `pi-agent-spike.mjs`

驗證 `@earendil-works/pi-agent-core` 能不能當我們缺的 Agent 層（藍圖 §6）。

**要跑之前：**

```bash
npm i @earendil-works/pi-agent-core @earendil-works/pi-ai
node spikes/pi-agent-spike.mjs
```

（這兩個套件**刻意沒有**加進 `package.json`——spike 而已，不要讓主專案
多背 110MB 相依。）

**驗證了三件事：**

1. 可以只註冊自己的領域工具，不必用 coding-agent 那套檔案／bash 工具
2. 可以接我們正在用的 Bedrock（`us.anthropic.claude-sonnet-4-6`）
3. 模型會真的呼叫工具、拿結果、用真數字回答

**實測輸出：**

```
── 完成（6929ms）──
事件序列: agent_start → turn_start → message_start → message_end
        → message_update → tool_execution_start → tool_execution_end
        → turn_end → agent_end
```

回答的板橋 96,548／新莊 82,069／中和 71,597 與直接查資料庫逐字相同。

**踩到的坑：** npm 版的內建模型註冊表是空的（`getBuiltinModels()` 回 0），
必須手工建 `Model` 物件。spike 裡那段就是。
