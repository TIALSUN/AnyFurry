# AnyFurry Beta 1 安全与隐私审查

审查日期：2026-10-01。当前候选版本：0.28.1，实际验证 Blender 5.2.0 LTS。

当前发布模型中的用户目录和编辑器路径历史已清除；网格、形态键和对象变换指纹与已确认最终版一致。异常提示中的 Windows 用户目录路径和带 filename 的文件异常路径已遮蔽。本次检查未确认插件源码存在联网外传、直接执行任意代码或高危凭据泄露。仍有两个应修复的输入与文件操作问题，历史安装包也仍有隐私痕迹，不能按新发行包的结论对外分享。

## 方法和边界

采用 OpenAI 官方 [security-best-practices skill](https://github.com/openai/skills/blob/main/skills/.curated/security-best-practices/SKILL.md)。该 skill 支持 Python，但没有 Blender 或桌面 Python 专用参考指南；本次补充采用插件源码审阅、AST 检查、受控后台 Blender 测试和 Python 官方文档。不是第三方安全认证。

检查范围是八个当前插件 Python 模块、当前内置模型、发布包内容和历史 ZIP 的路径及缓存痕迹。模型检查禁用自动脚本执行，并检查文本块、驱动、外部库和图片。源码扫描检查直接危险调用、导入和常见凭据模式，不能覆盖混淆代码、动态间接调用或所有个人信息格式。没有将项目文件上传到任何外部扫描服务。

不包含 Blender 可执行程序、操作系统、其他插件、所有滑块组合、鼠标逐项操作以及打印佩戴验证。路径遮蔽不保证隐藏任意文本中的所有姓名、邮箱或业务信息。

## 中等优先级

### 001 — 参数 JSON 在大小限制之前完整读取与解析（待修复）

位置：`blender_addon/anyfurry_mouth/project.py:156`。

载入参数先执行 `read_text` 和 `json.loads`，再验证格式和参数范围。用户导入不可信的超大或深层嵌套文件时，可能大量占用内存或阻塞 Blender；这不是网络可达漏洞，也未确认能够执行代码。Python [JSON 官方文档](https://docs.python.org/3/library/json.html) 建议限制不可信输入的数据大小。

落地建议：只接受常规文件；最多读取 64 KiB 加一个字节，超过即拒绝；保持现有格式与范围验证，并拒绝非有限数值。增加超大、嵌套和异常文件验证，确认错误时原参数保留。

## 较低优先级

### 002 — STL 临时文件名固定（待修复）

位置：`blender_addon/anyfurry_mouth/project.py:230` 至 `project.py:242`。

导出使用目标文件名加 `.anyfurry.tmp`，可能截断用户已有同名邻居文件；异常清理也可能删去该路径上的文件。目录中存在可预先写入路径的另一程序时，还有条件性的链接或竞争问题。没有据此确认权限提升。

落地建议：在目标目录用 `NamedTemporaryFile` 独占创建随机名称，只清理由本次导出创建的文件，成功后再替换最终目标。依据 [Python tempfile 文档](https://docs.python.org/3/library/tempfile.html)。新版构建器已采用该方式，但插件的 STL 导出尚未修改。

## 已处理的隐私项

### 003 — 模型携带本机目录与编辑器历史（新发行包已处理；历史版本仍待清理）

位置：旧 `assets/mouth_eye_base_v0270.blend`；当前载入点 `blender_addon/anyfurry_mouth/mouth.py:128`。

旧模型二进制有两个用户目录路径匹配，另有保存的文件浏览器历史。新发行包只包含 `assets/mouth_eye_base_v0281.blend`：重新写出所需场景依赖，去掉不需要的编辑器状态，将单个残留的文件全局路径替换为中性名称；未覆盖旧模型。

新资产用户目录路径匹配为 0，文本块、驱动、外部库、图片数量均为 0。网格、形态键及变换前后 SHA-256 均为 `b531c5f53e9069789b29c227d454cc860816d5106d87d4b6bd0fac877d078de4`。记录见 `blender_addon/validation/asset_privacy_verification.json`。

### 004 — 历史缓存含编译路径（新发行包排除；历史版本仍待清理）

历史部分 ZIP 含 `__pycache__`、`.pyc` 与 `.blend1`。编译缓存可能携带本机路径，备份也可能保留旧元数据。新版采用文件白名单，不包含这些文件。39 个历史 ZIP 的扫描清单位于本地 `blender_addon/security_review/historical_package_scan.json`，不进入发行包或 Git。

自动审批拒绝了批量原地覆盖历史 ZIP、模型并删除缓存及备份，理由是范围广且难以恢复。已改为创建单独的新脱敏资产与发行包；旧文件未被删除。Git 忽略文件不会自动清除其磁盘占用，也不会为这些未提交的旧版本建立恢复历史。

### 005 — 错误提示可能暴露用户文件路径（已处理）

位置：`blender_addon/anyfurry_mouth/privacy.py:7`、`recovery.py:87`、`mouth.py:142`、`project.py:141`、`project.py:165`、`project.py:259`。

统一过滤带 filename 的异常和常见 Windows 用户目录及 Linux home 路径，截断面板错误长度。五组用虚构身份构造的路径测试通过。插件本身的提示已覆盖；Blender 自身终端输出和用户完整工程文件不在此过滤器覆盖范围。

## 发布验证和后续

候选源码通过：完整模型载入、重复载入与独立场景、共享表面刷新、自动和手动刷新、保存重开、预设往返及无效预设保留原值、异常恢复、A/B/C 组合几何、STL 毫米制回读、撤销重做和启停。三组组合几何非流形边数量均为 0；默认 STL 为 206062 个三角形，回读尺寸约 259.693 × 195.229 × 288.351 mm。

发布构建核对版本、源码和模型哈希，检查包内路径和凭据模式，只生成 `blender_addon/dist/AnyFurry_Beta1.zip`。Git 只纳入当前发行源码、验证记录及一个 LFS 模型。001、002 可作为后续两个独立修复；历史文件清理由明确批准的范围执行。
