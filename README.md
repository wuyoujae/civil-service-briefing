# 公考晨间新闻简报

这是一个可直接发布到 GitHub Pages 的静态站点。

## 本地预览

直接打开 `index.html`，每日简报在 `archive/` 目录。

## 发布到 GitHub Pages

1. 在 GitHub 创建一个 public 仓库，例如 `civil-service-briefing`。
2. 在本目录执行：

```bash
git init -b main
git add .
git commit -m "Initial briefing site"
git remote add origin https://github.com/<你的用户名>/civil-service-briefing.git
git push -u origin main
```

3. 推送后，`Deploy GitHub Pages` workflow 会发布站点。
4. 访问地址通常是：`https://<你的用户名>.github.io/civil-service-briefing/`
5. `Daily news brief` workflow 会在北京时间周一至周五 10:00 左右自动生成当天简报并更新目录页。

## 说明

- GitHub Actions 的计划任务可能有数分钟延迟。
- 个别中文站点在 GitHub Actions 网络环境下可能偶发超时；脚本会继续生成并在简报里标注检索受限。
- 如需在中国法定节假日/调休工作日精准运行，需要额外接入工作日历。
