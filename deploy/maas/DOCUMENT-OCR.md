# 文档异步任务接入

P4–P6实施中，尚未部署M2。复用现有 `/maas/v1/artifacts`、`/maas/v1/tasks`、任务取消、结果下载和usage查询；不新增PDF专用API。模型服务M1见[验收报告](../../results/unlimited-ocr-2/20261116-m1/REPORT.md)。

PDF预处理使用 `requirements-document.txt` 固定的pypdfium2；不改变旧图片OCR的10 MiB/20MP契约。PDF要求真实格式、无加密且1–10页；150 DPI默认、最高300 DPI，每页最多10MP、合计50MP，超出预算拒绝，不静默丢页或降采样。图片经校验和EXIF方向归一化，输出坐标依据归一化后的页面尺寸。

PDF解析/渲染在独立进程执行：Linux地址空间512 MiB、CPU时间15秒、单文件64 MiB、64个文件描述符；外层检查墙钟期限（检查5秒、渲染30秒）和任务取消。每进程最多一个预处理任务，忙时返回429。Linux父进程退出会杀死预处理子进程；正常、取消及错误路径清理私有临时目录。部署文档容器还须使用有界tmpfs及cgroup，进程被强杀后由容器重启清除其临时卷；此约束不代表VM重启验收。

本机测试：安装 `requirements-maas.txt` 和 `requirements-document.txt` 后执行 `python -m pytest tests/maas/test_documents.py -q`。测试覆盖真实PDF渲染、损坏/加密/超页数/像素预算、取消清理及图片方向；Linux资源限制和实际GPU/下游链路仍须单独验收。
