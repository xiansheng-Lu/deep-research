# AI 研究者助手 · M2-6 信源元数据抽取阶段技术方案

- 版本：v0.1（实施前评审稿，2026-09-14；实现冻结后升 v1.0）
- 范围：证据信源四元组（来源域名/发布时间/信源类型/可信分级）的真实抽取与落库、按子问题的相关性打分与排序、跨子问题去重口径固化、缺失元数据标记；**零新增 REST/WS 契约、零数据库迁移**
- 上游依据：《AI研究者助手-软件需求规格说明书》§6 流程 5「材料标准化与溯源」（统一抽取域名/发布时间/类型/可信分级、去重并按相关性打分）、§8（可信分级字段必填、元数据缺失标记「来源信息不全」）；《AI研究者助手-软件开发计划》§3 M2-6、§5.1/§5.2（M2-6 不直接对应 A 项，A2 溯源覆盖率由 M2-7 承接）；《AI研究者助手-后端详细设计》§5.3.7 Evidence、§6.5.4 researcher、§6.5.5 standardizer；《AI研究者助手-前端详细设计》§10.7 枚举（SourceType/SourceLevel/Credibility）、§11.3 EvidenceCard/SourceBadge；《AI研究者助手-前端M2任务分解与方案设计》WP-8/WP-13/WP-14（M2-6 随 evidence 字段核对，「不另设对接点」）
- 编号口径：以《软件开发计划》§3 M2 里程碑表为唯一事实源——**M2-6 信源元数据抽取 → M2-7 数据点级溯源**（2026-09-14 编号漂移已统一，详设 §15.2 已同步）

## 1. 目标与范围

M1/M2-4 已把证据链路跑通并经看板暴露，但元数据是「字段在、值不准」：真链证据的 `source_type/source_level/credibility/relevance_score/published_at` 存在系统性失真（见 §2.2）。前端 SourceBadge/EvidenceCard 与 M2-4 冻结的 `EvidenceResponse` 已经按五值枚举在消费，等的是**真实值**而不是新接口。本包交付：

1. **信源类型五值判定**：`official_doc / news / community / search / internal` 按域名规则表真实分类，修正博查 web 命中被一律标 `news` 的错误。
2. **来源等级判定增强**：扩充以中国公域为主的权威域名规则表，三级（primary/secondary/tertiary）真实覆盖政府/统计/监管/学术/主流媒体/社区。
3. **相关性打分落地**：对每条证据按所属子问题计算 lexical relevance（零额外 token），与 Provider 自带分数（Tavily）融合，写入 `relevance_score` 并参与排序；修复真链该字段恒 0 的缺陷。
4. **可信分级模型修正**：credibility 与 relevance 解耦——信源权威性决定基准档，相关性只做轻微修正并给 primary 设置地板档，修复「政府站也评 C」的失真。
5. **发布时间多级回退**：Provider 日期优先；缺失时按预算抓取页面 HTML，经 trafilatura 元数据抽取补采；再缺则置空并在元数据中标记。
6. **元数据溯源留痕**：`Evidence.metadata_`（JSONB，0001 已建、当前从不写入）记录分类依据、打分构成、发布时间来源与缺失字段，供 M2-7/M3 与质量复盘使用。
7. 去重口径固化：保留现有 URL 规范化 + fingerprint 精确去重；近重复/证据簇聚类明确不做。

### 1.1 非目标（本包明确不做）

- **近重复与证据簇聚类**：simhash/minhash/语义聚类、转载稿合并均不做（SDP §3 M2-6 括号明示「不实现证据簇自动聚类」）；跨 URL 同文识别随 M3 检索强化评估。
- **internal 私域信源**：`source_type=internal` 是 M5 连接器/知识库产物，本包分类器永不输出 internal。
- **LLM 信源评判/向量语义相关性**：分类与打分全部确定性规则与词面算法，零额外 token；embedding 相似度随 M5 私域检索引入。
- **域名规则后台配置化**：规则表为代码内常量（带注释、可评审、可单测），不建 DB 表、不做管理端、不支持用户自定义可信域名。
- **全量网页爬取管线**：不做 robots 解析、JS 渲染、代理池、重试队列；页面补采是「缺失日期的一次性小预算抓取」，R3 的多供应商混合抓取策略在 M3。
- **报告 blocks / 论断绑定 / 点击回溯**：属 M2-7；本包只保证喂给 M2-7 的证据元数据真实可信。
- **REST/WS 契约扩字段**：`metadata_` 本包不对外暴露，「来源信息不全」UI 标记待 M2-7/M3 随结构化报告批次冻结；本包对前端是**字段值质量修复**，字段集零变化。
- **新 OpenAPI 快照**：无端点/无 schema 变更，契约继续冻结在 `docs/contract/openapi-m2-5.json`，本包不产出 openapi-m2-6。
- **历史已落库证据回刷**：只影响新 run；存量行的元数据修正不做回填（M2 内部试用前数据无保留价值，M2-8 生命周期治理时如需再评估）。

## 2. 现状盘点（M2-5 基线）

### 2.1 已具备（复用，不重复造）

| 能力 | 位置 | 现状 |
| --- | --- | --- |
| 证据表 | `app/db/models/evidence.py`（0001 已建） | domain/published_at/source_type(16)/source_level/credibility/relevance_score/fingerprint/metadata_(JSONB) 列齐全，**本包无需迁移** |
| 域名抽取 | `researcher_fan_out.py::_extract_domain` | urlsplit 取 host、去 `www.`，落 EvidenceDict.domain |
| Provider 日期 | `bocha.py::_parse_published_at`、`web_search.py::_parse_iso` | 博查 `datePublished`（ISO/RFC2822 宽松解析）、Tavily `published_date` |
| URL 规范化去重 | `app/retrieval/dedup.py` | 去 fragment/www、过滤 utm 等跟踪参数；fingerprint=SHA256(规范化URL + 截断正文)；retrieval 层同指纹保留高分、standardizer 跨子问题同指纹保留首条 |
| 等级/分级启发式 | `standardizer.py::classify_source_level/classify_credibility` | 后缀表 + relevance 阶梯（但规则集与输入都有问题，见 §2.2） |
| 排序与引用收敛 | `standardizer.run` | credibility 升序（A→D）+ relevance 降序；去重后收敛 sub_questions.evidence_ids |
| 分类落库与帧 | `persist_evidence` + `evidence.fetched` | 分类后证据幂等写库，帧载荷取落库权威字段（M2-4 冻结，含七项元数据） |
| 词面算法先例 | `critic.py::_tokenize/_jaccard` | CJK 单字 + 拉丁连续段切词、Jaccard 相似度，已用于冲突检测，打分器沿用同一分词口径 |
| 正文抽取依赖 | `trafilatura 1.12.2` + readability-lxml（已在锁文件） | 已验证 1.12.2 提供 `fetch_url(url)` 与 `bare_extraction(html, with_metadata=True)`（返回 date/author/sitename/title 等），**页面元数据补采无需新增依赖** |
| 前端消费面 | M2-4 `EvidenceResponse` + WS `evidence.fetched`、WP-14 SourceBadge/EvidenceCard | 五值 source_type、三级 source_level、A~D credibility、published_at 已全部按真实字段渲染 |

### 2.2 缺口（本包要补，按严重度排序）

1. **relevance_score 真链恒 0（核心缺陷）**：`researcher_fan_out._hit_to_evidence_dict` 不写 `relevance_score` 键，ORM 默认 0.0；博查不返回相关性分数（`bocha.py` 恒置 0.0），Tavily 分数又不写入证据。结果 standardizer 的分级公式把**所有**证据降两档：primary(A)→C、secondary(B)→D。现有 `test_standardizer.py` 用手塞 score 的假状态断言，掩盖了真链问题。
2. **source_type 假分类**：fan-out 用 `"news" if hit.source==WEB else "search"`，博查链路全部 WEB 命中 → 政府公告/统计数据/论坛帖一律标 news；五值枚举从未真实产出。
3. **来源等级规则集过窄**：仅 6 个后缀（.gov.cn/.gov/.edu.cn/.edu/.ac.cn/.ac）+ 13 个英文媒体关键词；中国公域核心权威源（部委与地方 gov.cn 子站、人民网/新华网/央视、主流财经媒体等）与社区站点（知乎/CSDN/Stack Overflow 等）均未覆盖，且「关键字包含」易误判（如含 `news` 的普通域名）。
4. **发布时间无页面级回退**：trafilatura 以 `with_metadata=False` 抽取，Provider 不返日期时 `published_at` 永久为 null；博查链路不抓取 HTML，没有任何二次获取通道。
5. **ranker 为空占位**：`app/retrieval/ranker.py::rerank` 直接返回原序，SDP 要求的「按与子问题的相关性打分」不存在。
6. **metadata_ 从不写入**：元数据缺失（PRD §8 要求标「来源信息不全」）与分类/打分依据均无承载。
7. **state 类型缺口**：`EvidenceDict` 未声明 `relevance_score`/`metadata_`，测试靠 `# type: ignore` 塞值。

## 3. 组件与代码落位

| 组件 | 位置 | 说明 |
| --- | --- | --- |
| 信源规则表与分类器 | `app/retrieval/source_rules.py`（新增） | 域名规则常量 + `classify_source(domain) -> SourceClassification`（type+level）；纯函数、零 IO |
| 相关性打分器 | `app/retrieval/ranker.py`（改造） | `score_relevance(query, *, title, snippet, content)` 与 `blend_score(provider, lexical)`；`rerank(hits, query)` 按分排序 |
| 页面元数据补采 | `app/retrieval/page_metadata.py`（新增） | `fetch_page_metadata(urls)`：httpx 受控并发抓 HTML + trafilatura bare_extraction，回传 date/sitename；宽松日期解析复用统一工具 |
| fan-out 接入 | `app/orchestrator/nodes/researcher_fan_out.py`（改造） | extract 后补采缺失日期；逐 hit 算 relevance 写入 EvidenceDict；source_type 占位由假 news 改为中性 `search`（权威分类由 standardizer 落定） |
| 标准化节点 | `app/orchestrator/nodes/standardizer.py`（改造） | 调 source_rules 重判 type/level；credibility 新模型（§5.4）；写 metadata_ 留痕 |
| state 类型 | `app/orchestrator/state.py`（改造） | EvidenceDict 增 `relevance_score: NotRequired[float]`、`metadata_: NotRequired[dict]` |
| 落库 | `app/orchestrator/persistence.py`（微调） | persist_evidence 透传 metadata_（当前未写该列） |
| 配置 | `app/core/config.py`、`.env.example` | 页面补采开关与预算（§10） |
| 测试 | `tests/test_source_rules.py`、`tests/test_ranker.py`、`tests/test_page_metadata.py`（新增）；`test_standardizer.py`、`test_researcher_fan_out.py`、`test_persistence.py`（更新）；`tests/test_integration_m26.py`（新增真库） | AC 见 §9 |

## 4. 任务分解与实现顺序

### T1 信源规则表与五值类型/三级判定（前置，无外部依赖）

`source_rules.py` 域名表 + 分类纯函数；standardizer 接入替换现有硬编码；fan-out 占位修正为中性 search。单测先行，规则表逐条可验。

### T2 相关性打分与排序（与 T1 并行）

ranker 词面打分 + Provider 融合；fan-out 逐 hit 计算并写入 evidence；EvidenceDict 补字段；排序口径不变（standardizer 仍 credibility→relevance）。

### T3 页面元数据补采（独立，可并行）

page_metadata 抓取器（开关/预算/超时）+ trafilatura 元数据解析；fan-out 在 extract 后对缺日期命中补采；失败静默降级。

### T4 可信分级模型重构（依赖 T1/T2）

standardizer 切换为「权威基准 + relevance 至多降一档 + primary 地板 B」；更新 test_standardizer 全部断言（行为修正，非回退）。

### T5 元数据落库与全链路口径收口

metadata_ 写入（分类依据/打分构成/日期来源/缺失字段）、真库集成（m26_* schema）、契约零变化比对、全量门禁。

## 5. 关键设计

### 5.1 元数据抽取总链路

```text
Provider search（bocha/tavily）
  └─ RetrievalHit{url,title,snippet,content,score?,published_at?}
       │  fan-out：normalize 去重（既有）
       ├─ T3 仅对 published_at 缺失者：fetch_url+bare_extraction 补 date/sitename（预算内、失败降级）
       ├─ T2 score_relevance(子问题, title+snippet+content) → blend(provider 分)
       └─ EvidenceDict（retrieve 内存态：source_type 占位 search、level tertiary）
            │  standardizer（落库权威）
            ├─ T1 classify_source(domain) → source_type + source_level
            ├─ 跨子问题 fingerprint 去重（既有规则不变）
            ├─ T4 credibility(source_level, relevance, 类型信号) → A/B/C/D
            ├─ T5 metadata_{规则命中、分数构成、日期来源、缺失字段}
            └─ 排序 credibility↑ → relevance↓ → 落库 + evidence.fetched（载荷不变）
```

### 5.2 信源规则表与分类判定（T1）

规则表为 `source_rules.py` 内的分节常量，每条带中文注释，支持两种匹配：

- **后缀规则**（`.domain` 形态）：host == domain 或 host 以 `.domain` 结尾（如 `gov.cn` 命中 `stats.gov.cn`、`miit.gov.cn`；`edu.cn` 命中 `tsinghua.edu.cn`）。
- **精确 host 规则**：不带点前缀的完整 host（用于 `who.int` 这类不以通用后缀区分的机构）。

判定优先级：**精确/后缀规则命中 → 关键字兜底 → 默认 tertiary/search**。规则分三张表：

| 表 | 产出 source_level | 产出 source_type | 覆盖样例（初版，评审时增删） |
| --- | --- | --- | --- |
| `PRIMARY_OFFICIAL` | primary | official_doc | `gov.cn`/`gov`（各级政府、部委、统计局、监管）、`mil`、国际组织 `who.int`/`un.org`/`imf.org`/`worldbank.org`/`oecd.org`/`wto.org` |
| `PRIMARY_ACADEMIC` | primary | official_doc | `edu.cn`/`edu`/`ac.cn`/`ac`、`arxiv.org`、`doi.org`、`cnki.net`、`nature.com`、`science.org`、`ieee.org`、`acm.org`（学术/出版机构的正式文献，按详设「学术机构→primary」口径） |
| `SECONDARY_NEWS` | secondary | news | 通讯社/广电/主流报刊：`xinhuanet.com`、`people.com.cn`、`cctv.com`、`cntv.cn`、`china.com.cn`、`thepaper.cn`、`caixin.com`、`reuters.com`、`bloomberg.com`、`bbc.com/co.uk`、`nytimes.com`、`wsj.com`、`ft.com`、`economist.com` 等；保留现有英文关键词作弱兜底但收紧为整词匹配 |
| `TERTIARY_COMMUNITY` | tertiary | community | UGC/问答/论坛/博客平台：`zhihu.com`、`weibo.com`、`csdn.net`、`jianshu.com`、`juejin.cn`、`stackoverflow.com`、`reddit.com`、`github.com`（讨论区/issue）等 |
| 默认 | tertiary | search | 无法归类的普通网页/机构站/聚合页 |

判定细则：

1. 单一域名只可能命中一张表，按 PRIMARY_OFFICIAL → PRIMARY_ACADEMIC → SECONDARY_NEWS → TERTIARY_COMMUNITY 顺序首中即止；`ac.cn` 等歧义域以后缀表顺序固化。
2. 取消「domain 包含某关键词即 secondary」的子串匹配（避免 `fake-news.example.com` 类误判）；新闻兜底关键字改为对最后两级 host 的整词比较，且只在四张表全未命中时使用。
3. 空域名/IP/内网 host → (tertiary, search)，不抛异常。
4. `internal` 不由本分类器产出（M5 由检索来源 `RetrievalSource.KNOWLEDGE/CONNECTOR` 直接赋值）。
5. 规则表是评审对象：初版覆盖以 PRD 公域信源（官方文档/新闻/社区）与前端 demo fixture 中出现的域名为基线，后续按真实检索结果迭代，迭代必须带单测样例。

### 5.3 相关性打分（T2，零 token）

纯函数 `score_relevance(query, *, title, snippet, content=None) -> float`，复用 critic 的中英混合切词口径（CJK 单字、拉丁连续段）：

- `coverage`：query 词项在证据文本中的覆盖率（命中项/query 有效项，剔除停用字后）；
- `title_hit`：query 词项在标题中的覆盖率（标题权重单独计）；
- `body_sim`：query 与 `title + snippet + content[:2000]` 的 Jaccard；
- 合成：`lexical = 0.5*coverage + 0.3*title_hit + 0.2*body_sim`，clamp 到 [0,1]，保留三位小数；query 无有效词项时返回 0.0。

Provider 融合（`blend_score`）：

- Tavily 返回有效分（>0）：`blended = round(0.5*provider + 0.5*lexical, 3)`；
- Provider 无分（博查恒 0、解析失败）：`blended = lexical`；
- 不为融合引入 LLM 或向量。

写入与排序：

- fan-out 在 extract 去重后逐 hit 计算（query 即所属子问题问题文本），写入 `EvidenceDict.relevance_score`；跨子问题去重存活的首条证据保留其原子问题的打分（不重算、不换属主，避免 evidence_count 抖动）。
- `ranker.rerank(hits, query)` 同步落地为按 blended 降序，供 retrieval 层/单测复用；fan-out 内证据顺序按分数排，standardizer 的最终顺序仍为 credibility→relevance（权威优先的既定语义）。

### 5.4 可信分级模型（T4，与相关性解耦）

可信度回答「信源本身是否权威」，相关性回答「与本子问题是否相关」，二者耦合是当前失真的根因。新模型：

| 基准（来自 source_level） | relevance ≥ 0.2 | relevance < 0.2 |
| --- | --- | --- |
| primary | A | **B（primary 地板，不再降到 C/D）** |
| secondary | B | C（至多降一档） |
| tertiary | C | D |

修正点相对现实现：

1. 低相关的降档步数从 2 档收敛为 1 档，并设 primary 地板 B——权威源词面弱相关应在**排序**中靠后，而非被否定可信度；
2. 类型为 community 的 tertiary 证据已天然 C/D，不再附加惩罚；
3. 缺失发布时间**不参与降级**：大量非新闻权威页（政策文件、技术文档）本就无发布日期，缺失在 metadata_ 标记即可（PRD 的「来源信息不全」是展示提示，不是可信度判决）；
4. credibility 的全部输入仅为 (source_level, source_type 与 level 同源得出, relevance_score)，规则确定性、可枚举，单测覆盖全矩阵。

### 5.5 发布时间多级回退与页面补采（T3）

来源优先级：`provider.datePublished`（现有解析） → 页面 HTML 元数据 → null。

页面补采器 `fetch_page_metadata(urls)`：

- 触发条件：仅当 `SOURCE_PAGE_METADATA_ENABLED=true` 且该 hit 无 provider 日期；
- 预算：每子问题最多补采 `SOURCE_PAGE_FETCH_PER_SUBQUESTION` 条（默认 3，按 fan-out 去重后顺序取前 N 条），单页超时 `SOURCE_PAGE_FETCH_TIMEOUT_SECONDS`（默认 5s），asyncio 信号量限并发；整个 retrieve 仍受既有 60s 单子问题超时兜底；
- 实现：`trafilatura.fetch_url(url, no_ssl=False)` 取 HTML → `bare_extraction(html, with_metadata=True, favor_recall=True, as_dict=True)` 取 `date`/`sitename`/`author`；日期经宽松解析（ISO/RFC2822/`YYYY-MM-DD`，统一 UTC 处理），解析不出视为缺省；
- 降级：网络异常/超时/空页/反爬页/JS 渲染页一律静默跳过，published_at 保持 null，**不影响证据入库与子问题成功状态**（与 PRD「单源抓取失败降级」一致）；
- 补采到的 `sitename` 只写入 metadata_，不反解域名分类（域名是稳定信号，站点自报名不可信）；
- 不做 robots 解析、Cookie、代理与重试（非目标，§1.1）；出站请求带常规浏览器 UA 与系统标识，不携带任何用户凭据。

### 5.6 去重口径（固化，不扩边界）

- 保留 retrieval 层与 standardizer 两层既有去重：规范化 URL（去 www/fragment/跟踪参数）+ SHA256(规范化URL + 正文前 2000 字符)；
- standardizer 跨子问题去重仍保留**首条**（属主子问题不变），子问题 evidence_ids 收敛逻辑不变；
- 同内容不同 URL 的转载/镜像不合并（fingerprint 含 URL 键，本包不改）——近重复聚类是明确的 M3 非目标；
- 页面补采不参与 fingerprint（fingerprint 在补采前已基于 provider 正文/摘要确定，避免抓取成败导致同一 URL 指纹漂移）。

### 5.7 元数据留痕（T5）

分类后每条证据写 `Evidence.metadata_`（0001 JSONB 列，persist_evidence 增透传）：

```json
{
  "source_rule": "PRIMARY_OFFICIAL:gov.cn",
  "relevance": {"provider": 0.0, "lexical": 0.62, "blended": 0.62},
  "published_at_source": "provider | page | null",
  "site_name": "国家统计局",
  "missing_fields": ["published_at"]
}
```

- `source_rule` 记录命中表与规则（可复盘误判）；`missing_fields` 从 `[published_at]` 中按实际缺失生成（domain/type/level 必有，不列入）；
- metadata_ 本包**不进任何 REST/WS 响应**（M2-4 契约零变化）；PRD「来源信息不全」标记的 UI 呈现随 M2-7 结构化报告批次或 M3 证据详情增强再冻结对外字段；
- 重放幂等：persist_evidence 按 ID 插缺失，已有行不覆盖，metadata_ 随首次落库固化。

## 6. 契约影响（本包对前端零变化）

- 不新增/不修改任何端点；`GET /runs/{id}/evidence`、`GET /runs/{id}/evidence/{evidence_id}`、WS `evidence.fetched` 字段集与 openapi-m2-5 完全一致。
- 值语义变化（前端需知晓、**无需改代码**）：
  - `source_type` 不再几乎全是 news，会真实出现 official_doc/community/search；
  - `credibility` 分布变真实（权威源可见 A/B），SourceBadge 既有色编码直接受益；
  - `relevance_score` 从恒 0 变为真实 0~1 小数（前端当前不展示该值，M2-7 排序/绑定消费）；
  - `published_at` 命中率上升（补采成功时）。
- 前端枚举映射（官方文档/新闻/社区/搜索/内部、A~D、三级来源）已在 WP-8 完成，本包是其真实数据面；交接单以「字段核对 + 分布抽验」方式回归，不设新对接点（与前端 M2 WP 分解 §4 一致）。

## 7. 数据模型与迁移

- **无新迁移**：复用 0001 的 evidence 表全部列；alembic head 保持 0004。
- `metadata_`（模型属性名，列名 `metadata`）开始真实写入；历史行该列为默认 `{}`，不回刷。

## 8. 与 LLD / 前端设计的偏离点

1. **credibility 与 relevance 解耦**：LLD §6.5.5 伪代码隐含「等级+relevance 联合决定可信度」，M1 实现按 2 档降级；真链 relevance 恒 0 导致全面失真。本包保留「联合」外形但收敛为至多 1 档并设 primary 地板，语义上以「权威性为基准、相关性为微调」为准，已在 §5.4 全矩阵列明。
2. **retrieve 占位 source_type 改为中性 search**：现实现把 WEB 命中占位为 news；占位值不应伪装成分类结果，权威分类以 standardizer 落库为准。
3. **页面级发布时间补采是新增环节**：LLD §6.5.4 伪代码只有 search/fetch（正文），未定义元数据补采；本包以小预算、可关闭、静默降级的方式补该环节，不改变子问题成功/失败状态机。
4. **学术出版商归 primary/official_doc**：nature/science/ieee/acm/arxiv/doi 等按详设「学术机构→primary」归并；其页面若实为新闻/博客内容不做二次细分（规则只看域名，M2 不做页面级类型识别）。
5. **不发 openapi-m2-6 快照**：M2-3 起每批冻结快照的前提是有契约增量；本包零契约变化，按「派生产物随事实源变化才重新生成」原则不产空快照。

## 9. 测试与门禁

离线全假替身；真库用例独立 schema（前缀 `m26_`），setup/teardown 各一次 DROP SCHEMA CASCADE。

| 编号 | 判定要点 |
| --- | --- |
| AC-1 | 规则分类：gov.cn 部委/地方子站、who.int → primary/official_doc；清华 edu.cn、arxiv、nature → primary/official_doc；新华网/人民网/路透/BBC → secondary/news；知乎/CSDN/Stack Overflow → tertiary/community；普通企业站 → tertiary/search；空域名/IP → tertiary/search 不抛错 |
| AC-2 | 子串误判修正：`fake-news.example.com`、含 news 字样的非媒体域不判 news；同一 host 多表命中按优先级表首中（给出歧义样例锁定顺序） |
| AC-3 | relevance：query 词命中标题+摘要的证据分数高于无关证据；query 无有效词项 → 0.0；分数恒在 [0,1]、三位小数；内容超长只取前 2000 字符参与 |
| AC-4 | 融合：provider=0（博查形态）取 lexical 非零；provider>0 时 0.5/0.5 融合；fan-out 落库证据 relevance_score 真链非恒 0（离线替身断言每条都有分） |
| AC-5 | credibility 全矩阵：primary 高相关 A/低相关 B（地板）；secondary B/C；tertiary C/D；排序 A→D 且同级按 relevance 降序 |
| AC-6 | fan-out：占位 source_type=search（不再出现无依据 news）；EvidenceDict 含 relevance_score；补采/打分失败不改变子问题终态 |
| AC-7 | 页面补采：provider 缺日期 + HTML 含发布时间元数据 → published_at 补写、metadata_.published_at_source="page"；provider 已有日期不触发抓取；开关关闭不抓取；超时/异常/反爬静默 null 且证据正常入库；每子问题抓取数不超配额 |
| AC-8 | 去重不回归：URL 跟踪参数/www/fragment 归一去重、跨子问题首条保留与 evidence_ids 收敛、补采成败不改变 fingerprint（既有 fan-out/standardizer 用例全保留） |
| AC-9 | metadata_ 落库：含 source_rule、relevance 三值、published_at_source、missing_fields；无日期证据 missing_fields 含 published_at；既有行/无 deps 路径不受影响 |
| AC-10 | 真库集成：m26_orm schema 下一条带权威域名的证据落库后 source_type/source_level/credibility/relevance_score/metadata_ 可独立会话读回且值正确（跑后 schema DROP 无残留）；alembic head 仍为 0004（无迁移） |
| AC-11 | 契约零变化：以当前 app.openapi() 重新导出与 docs/contract/openapi-m2-5.json 的 24 路径及 Evidence 相关 schema 比对为空 diff（不产生新快照文件） |
| AC-12 | 全量 pytest 零回退（本包更新的 test_standardizer 断言以 §5.4 新矩阵为准，不计回退）；本批文件 ruff 全净；mypy 相对基线零新增 |

## 10. 配置项变更

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SOURCE_PAGE_METADATA_ENABLED` | true | 是否对 Provider 缺发布时间的命中抓取页面补采元数据；关闭后发布时间仅取 Provider |
| `SOURCE_PAGE_FETCH_TIMEOUT_SECONDS` | 5 | 单页抓取超时（秒），超时静默跳过 |
| `SOURCE_PAGE_FETCH_PER_SUBQUESTION` | 3 | 每子问题补采页面数上限（只补缺日期者，0 等价关闭） |

## 11. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 域名规则表误判/不完备（权威站漏判、UGC 冒认官方） | 表数据集中评审、单测样例锁定；未知域一律 tertiary/search 不冒充权威；规则迭代必须带样例；source_rule 留痕支持真链复盘 |
| 页面补采拖慢 retrieve（每子问题最多 +3 个出站请求） | 默认配额 3、单页 5s、信号量并发、60s 总超时兜底；开关可一键关闭；只补缺日期者 |
| 目标站反爬/JS 渲染/境外不可达 | 静默降级 provider/null，不影响入库；不做重试队列与代理池（M3 R3 多供应商策略） |
| credibility 口径变化影响看板颜色分布 | 枚举与色编码不变，分布由失真变真实；交接单提示前端做分布抽验，确认无「按固定等级写死」的 UI 逻辑 |
| lexical 对博查二次摘要打分偏乐观（摘要本就贴着 query 写） | title/body 分权重摊薄；融合分仅用于排序与微调可信度，不产出给用户看的「准确率」结论；真链分数分布纳入交接观察项 |
| 补采增加目标站点负载 | 单 run 总补采数受层数×3 天然约束；只发 HEAD/GET 常规请求；无批量遍历 |
| 规则表与 M2-7 claim 绑定的耦合预期 | 本包只保证证据元数据真实；M2-7 的论断绑定算法另案设计，不在本包预埋接口 |

## 12. 交付与交接清单

1. 代码：T1~T5 全部文件（§3 落位表）、配置与 `.env.example`；无迁移、无新依赖；
2. 契约：**无新快照**，openapi-m2-5 保持冻结，AC-11 以重新导出空 diff 自证；
3. 文档：实现冻结后本文件升 v1.0 回填实测结论；`backend/README.md` M2 里程碑行回填 M2-6 状态（指针引 SDP §3）；
4. 联调：`docs/feedback/M2-6信源元数据抽取交接.md`（前端无新对接点，重点为 evidence 字段值分布抽验与 SourceBadge 回归），回归关闭后移入 archive/；
5. 不在本包的动作（证据簇聚类、internal 私域、LLM/向量评判、规则配置化、报告溯源 M2-7、历史数据回刷）以 §1.1 为准，流转 M3/M5/M2-7，不临时扩包。
