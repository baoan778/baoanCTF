---
name: ctf-osint
description: OSINT / 图寻 / 溯源打法：图片/账号/视频定位 → 元数据/OCR/图搜三路并进 → 时间线还原与坐标/街景线索；自带适用条件与「何时停」判据。当题目要求定位/溯源/情报检索，或给了图片要找出处/地点时参考。
license: MIT
compatibility: ctf-agent（Windows 宿主 + WSL 执行；本 Skill 只读，命令/工具/网络可用性按题探测）
allowed-tools: Bash Read Write Edit Grep Glob
metadata:
  user-invocable: "false"
---

# OSINT / 图寻 / 溯源 打法（本机实测版）

## 适用 / 不适用 / 何时停（SoK 的 skill 定义：技能必须带适用条件与终止判据）

- **适用**：给了图片/账号/视频/昵称要求定位或溯源；坐标换算题；时间线还原；地图/街景/招牌线索。
- **不适用**：需要内部数据或物理接触的（门禁记录、员工名单）；纯逻辑题（走对应题型卡）；
  平台明确禁止外部协作的场次。
- **何时停**（任一命中就停手换路线或找人）：
  1. 同一目标的名字/坐标**猜 >30 次**未命中；2. 元数据/OCR/图搜**三条路都空**；
  3. 卡住 **20 分钟**且没有新的可验证线索；4. 需要"谁/在哪/何时"这类**现实世界事实**而图里没文字证据。
- **复现要求**：本卡的每条手法都要能给出**命令 + 原始回显**（例如 B站 API 的 URL 与返回片段），
  只可查的结论不算结论。

> **为什么单独建这张卡**：2026-10-01 的比赛里这一块是我们最弱的 —— Pwn/Web/Reverse/AI 全通，
> 而 OSINT 只有 1/3。失败姿势高度一致：**用"猜"代替"查"**（猜了 200+ 个建筑名、扫了 176 万点坐标，全空）。
> 这张卡把当时的教训固化成流程，避免下次重演。

## 0. 铁律（先立规矩再干活）

1. **先跑工具，不许开局就猜名字**：`py tools/imgintel.py <图> --blocks 3x3 --search "<关键词>"`
   —— 元数据 / 分块 OCR / 线索筛选 / 坐标换算 / 反查链接一次拿齐，全部落 `notes/osint-<图名>.md`。
2. **猜测有预算**：同一目标的名字/坐标猜 **>30 次**还没命中 → **停**，换路线或找人。
   （反例：我们那天把容器当 oracle 猜了 210+ 次，信息增益≈0。）
3. **一句人话顶 200 次猜**：视觉/地理题**优先找同事看一眼**（"这是哪"），拿到一句话再往下做。
   人机配合话术见 `prompts/human-hint.md`。
4. **坐标必须带口径**：`WGS84`（GPS/OSM/Google）→ `GCJ02`（高德/腾讯）→ `BD09`（百度）→ `BD09MC`（米）。
   换算用 `tools/imgintel.py --geo <lat> <lon>`；**口径搞错=差几百米，白做**。
5. **能自校验就用题目给的校验**：例如题目给了 flag 的 **md5**，就把候选算出来本地比对，
   **命中才提交**（不要拿平台当 oracle 试运气）。

## 1. 图片类线索的处置顺序

| 步骤 | 做什么 | 常见结果 |
|---|---|---|
| ① 元数据 | `exiftool -a -G1 -s 图` / `imgintel` 第 1 节 | **多数题会剥掉 GPS**（本次两张图都被剥）→ 别在这里耗 |
| ② 文字 | 整图 + **分块** OCR（角落常是暗示） | 照片类常"确实没有文字"（噪声要用**置信度**过滤，别拿噪声当线索） |
| ③ 视觉特征 | 列可检索特征：招牌 / 地标 / 建筑形态 / 车型 / 电话区号 / 路牌 | 这是真正的抓手（例：「梦田 LIVEHOUSE」招牌 → 门店地址 → 锚定城区） |
| ④ 反查 | 关键词图搜（Bing 可用）→ 命中后 **正向检索确认** | 反向图搜要**公网图床 URL**（本地文件不行） |
| ⑤ 锚定 | 把线索串起来定位（例：同系列另一题已知在南京 → 缩小到城区） | **跨题交叉**往往是最快的收敛方式 |

## 2. 文字/账号/视频类溯源（实测有效）

- **B站 API 是金矿**（`fetch_content` 直接打，不需要 key）：
  - 视频信息：`https://api.bilibili.com/x/web-interface/view?bvid=<BV>`
    （`desc` 简介 / `dynamic` / `pages[].part` 分P标题 / `pubdate` 发布时间 / `aid`）
  - 评论（找置顶/作者回复）：`https://api.bilibili.com/x/v2/reply?type=1&oid=<aid>&sort=2&ps=20&pn=1`
  - 弹幕（常有"暗号"提示）：`https://comment.bilibili.com/<cid>.xml`
  - 搜索：`https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword=<kw>`
- **旧站编号换算**：`av` 号 ↔ `BV` 号；`03019` 这种"补零"写法常就是老 `av` 号（本次就是靠这条破的题）。
- **博客/WordPress 站**能挖出比自己读页面多得多的东西：
  - REST API：`/wp-json/wp/v2/posts?search=<关键词>`、`/wp-json/wp/v2/comments?...`、`/wp-json/wp/v2/media?parent=<id>`
  - 媒体库的 `post` 字段能反查"这张图挂在哪篇文章下"；评论区常藏着作者自留答案（本次的彩蛋就在评论里）。
- **存档/镜像**：Wayback 在本机**测过不可达**，别把宝压在那上面；优先用站点自身的 REST API 与搜索引擎快照。

## 3. 时间线/地理位置题的惯用套路

- 时间类：原图 EXIF 的 `DateTimeOriginal`（精确到秒）> 平台/博客发布时间 > 视频 `pubdate`（时间戳要**换算成 UTC+8**）。
- 位置类：**招牌文字 > 建筑形态 > 公共设施（公交涂装/交通牌/路灯）> 植被天气**。
- 数值类：坐标小数位决定精度（6 位≈0.1m、4 位≈10m）；题目要求"四舍五入到个位"通常指 BD09MC 取整。

## 4. 常见坑（血账）

- **把 OCR 噪声当线索**：照片纹理会让 OCR 凭空造字（实测"二二/Hilt/Sane"满屏）→ 必须按 **tesseract 置信度**过滤。
- **用猜测填满时间**：猜名字/猜坐标能"看起来很努力"，但**信息增益接近 0**，且有提交次数成本。
- **口径混用**：把 WGS84 当 BD09 直接算墨卡托 → 差几百米。
- **忽略平台隐藏提示**：这次 id=34 的关键提示是藏在题面第二段的（"了解一下二维码的机制"）→ **开工先把 hint 抄成清单**。
- **不落盘**：跑完什么都没写 → 下次重来。**每个结论都写进 `notes/`，包括失败的路线。**

> ★ **看图先走 [vision-to-semantics.md](references/vision-to-semantics.md)**（视觉→语义四步流水线 + 语义清单模板 + 最值钱的 5 类线索；**别直接猜地名** —— 那正是 33% 的根因）
>
> **本技能 references 索引**（其余按需读）：[vision-to-semantics.md](references/vision-to-semantics.md) · [geolocation-and-media.md](references/geolocation-and-media.md) · [social-media.md](references/social-media.md) · [web-and-dns.md](references/web-and-dns.md)
