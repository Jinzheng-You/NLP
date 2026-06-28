# NLP：地书趣味交互系统

这是一个 Flask Web 项目，包含三个入口模块：

- 地书谐音梗小游戏
- 自然语言转图形语言
- 符号造句工坊

当前主页面是系统首页，用户可从首页进入不同模块。谐音梗小游戏负责题目挑战；自然语言转图形语言负责把输入句子按顺序转换为地书标签序列并展示对应图标。

## 项目结构

```text
NLP/
├── app.py
├── requirements.txt
├── README.md
├── data/
│   ├── questions_expanded_100.json
│   ├── usable_symbols_expanded.json
│   ├── labels_000001_end.json
│   └── labels_1_end_merged.json
├── auto_cut_segments/
│   └── *.jpg
├── templates/
│   ├── _navbar.html
│   ├── menu.html
│   ├── index.html
│   ├── game.html
│   ├── result.html
│   ├── symbols.html
│   ├── rules.html
│   ├── translator.html
│   └── symbol_sentence.html
└── static/
    ├── css/
    │   └── style.css
    └── js/
        ├── main.js
        └── translator.js
```

## 功能说明

### 地书谐音梗小游戏

- 支持设置玩家昵称、题目数量、题目类型、难度和随机出题。
- 支持查看提示、答题反馈、分数统计、错题回顾、错题再练和导出 CSV。
- 题目类型包括：
  - `homophone_choice`：根据符号读音和谐音猜答案。
  - `visual_idiom_choice`：根据符号含义和画面组合猜成语或短语。

### 自然语言转图形语言

- 用户输入自然语言后，系统按原文顺序生成地书标签序列。
- 支持保留标点，例如逗号、句号、问号、感叹号等。
- 如果输入末尾没有句末标点，系统会自动补充 `。`。
- 没有合适图标表达的片段会自动略过。
- 输出结果会展示标签序列和对应地书图片。
- API Key 可选：
  - 填写 API Key 时，由所选 API 服务进行自然语言到地书标签的语义转换。
  - 未填写 API Key 或远程服务不可用时，使用本地顺序匹配作为 fallback。

支持的 API 服务：

- DeepSeek
- 豆包
- 通义千问
- OpenAI-compatible

 ### 符号造句工坊

 - 系统随机抽取10个地书符号，用户可拖动符号到工作区进行排列组合。
 - 支持使用 DeepSeek、豆包、通义千问等 API 服务进行语义分析。
 - 分析结果包括自然语言翻译、解析说明、故事拓展（约100字的生动故事）。
 - 提供语义连贯性评分和创意评分（0-100分）。
 - 生成相关标签列表，帮助用户理解符号组合的语义。

 ### 音乐与音效

 - 背景音乐：页面加载后自动播放背景音乐（bgMusic.mp3），支持循环播放。
 - 点击音效：所有按钮、下拉菜单、单选框、复选框等交互元素点击时播放点击音效（click.mp3）。
 - 答题反馈：答对题目播放成功音效（true.mp3），答错播放错误音效（false.mp3）。
 - 操作反馈：无效操作（如无输入点击转换、无标签点击复制等）播放错误音效，成功操作播放成功音效 。
 - 连续播放：背景音乐通过 sessionStorage 保存播放位置，页面切换时自动恢复播放。

### 视觉互动

- 全站背景有地书小图标随机游走。
- 鼠标移动时，背景图标会轻微追随；离开影响范围后自动分散，避免聚集在一个点。
- 答题后或转换成功后，会触发地书弹幕回放。
- 弹幕图标会从屏幕边缘弹入并飘过，带有 q 弹缩放和轻微旋转效果。

## 数据文件说明

- `data/questions_expanded_100.json`：游戏题库。
- `data/usable_symbols_expanded.json`：游戏符号库。
- `data/labels_000001_end.json`：自然语言转图形语言使用的标签映射文件。
- `data/labels_1_end_merged.json`：保留的合并标签数据，供后续扩展或数据整理使用。
- `auto_cut_segments/`：地书图片数据集。

程序启动时会校验题目字段、答案选项和图片引用。发现问题会在控制台输出 warning，正常题目仍会继续运行。

## 安装依赖

```bash
cd NLP
pip install -r requirements.txt
```

## 运行方法

```bash
python app.py
```

启动后访问：

```text
http://127.0.0.1:5000/
```

## 主要路由

- `/` 或 `/menu`：系统首页。
- `/setup`：谐音梗小游戏设置页。
- `/game`：谐音梗小游戏答题页。
- `/result`：游戏结果页。
- `/symbols`：符号库浏览页。
- `/rules`：玩法说明页。
- `/translator`：自然语言转图形语言页面。
- `/symbol-sentence`：符号造句工坊页面。
- `/api/translator/translate`：自然语言转图形语言接口。
- `/api/translator/stats`：转换标签库统计接口。
- `/images/<filename>`：游戏图片读取接口。
- `/api/translator/segments/<filename>`：转换结果图片读取接口。

## 计分规则

- easy 答对 +10 分
- medium 答对 +15 分
- hard 答对 +20 分
- 答错不扣分
- 使用提示后答对，只获得一半分数

## 演示句子

自然语言转图形语言模块可以优先演示这些句子：

```text
我今天很开心，吃了好吃的。
```

```text
我和朋友出去玩，晚上回家睡觉。
```

```text
今天下雨，我和朋友在家玩。
```

```text
我用电脑工作，也听音乐。
```

```text
我坐车去上班，晚上回家。
```

## 注意事项

- 后端为 Python + Flask，前端为 HTML、CSS、JavaScript。
- 不要随意修改 JSON 中的 `filename` 字段。
- 不要随意修改 `auto_cut_segments/` 中的图片文件名。
- API Key 不会写入本地文件，只随当前请求发送到后端用于远程调用。
- 本项目使用 Flask session 保存游戏 ID，并用后端临时状态保存当前游戏过程，适合本地课堂演示。
