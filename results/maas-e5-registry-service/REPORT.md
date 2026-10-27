# 常驻仓库与OCR发布 · 2026-10-27

001测试环境常驻HTTPS仓库已上线，两个systemd单元enabled/active，原临时仓库仍停用。实测匿名401、reader读取200、reader写入401，publisher实际推送OCR新版本成功。入口仅LAN18443，后端127.0.0.1:18500，证书验证开启，无Docker/VM重启。

源OCR image/config ID保持可追溯，新发布增加公开标记层，使用新的manifest digest和image ID；具体值见release.json。新worker冷拉尚属下一feature，发布成功本身不表示拉取或OCR验收通过。

安装/权限/发布元数据：[installed.json](installed.json)、[access.json](access.json)、[release.json](release.json)。运维边界见[常驻仓库手册](../../deploy/maas/REGISTRY-SERVICE.md)。无高可用或生产RPO声明，未做VM重启验证。
