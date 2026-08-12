@echo off
chcp 65001 >nul
set DASHSCOPE_API_KEY=sk-ws-H.ERPDYDP.I2aO.MEUCIGhYnFfNGjFvddfc6JwAHahjr9hOky6QWCP-gGOKsOwEAiEArBlqrIdboZw7UvJCQbO1KxnXlrgodbu-Qx8dm-uNDxE
set BAILIAN_BASE_URL=https://llm-r6740qoni0waj8io.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
set CODENTUM_ENGINE_COMMAND_JSON=["python", "D:\\Run2AGI\\codentum\\packages\\engine\\codentum_engine\\__main__.py", "--project-root", "D:\\Run2AGI\\codentum", "--log-level", "WARNING"]
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

cd /d D:\Run2AGI\codentum\packages\desktop
npm run dev
