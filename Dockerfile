# =============================================================================
# cats-inbox-api 映像(二段建置)
#
# 🔴 為什麼二段:平台紅線禁止 `build: .` 在正式機一邊運行一邊編譯。
#    第一段裝依賴(含編譯期工具),第二段只留執行期需要的東西——
#    正式環境跑的映像裡沒有編譯器,攻擊面小,體積也小。
# 🔴 base image 釘到 patch 版:FROM 用 latest 會讓「同一份 Dockerfile 重建
#    出不同的東西」,而這種差異查起來沒有任何線索。
# =============================================================================

FROM python:3.13.1-slim AS builder

WORKDIR /build
COPY requirements.txt .
# 先把依賴裝進獨立前綴,第二段整包複製,避免把 pip 快取與編譯工具帶進執行期映像
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.13.1-slim

# 🔴 non-root:容器被打進來時的破壞半徑差在這幾行。UID 固定 1000,
#    與平台其他服務一致(volume 權限才不會因映像重建而漂移)。
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin appuser

COPY --from=builder /install /usr/local
WORKDIR /srv/app
COPY --chown=appuser:appuser app/ ./app/

USER appuser

# gateway 以 `cats-inbox-api:8080` 反代;埠號是與 portal 的契約的一部分。
# 只 EXPOSE,不由容器對主機發布——全 VM 只有 gateway 對外。
EXPOSE 8080

# 🔴 綁 0.0.0.0:綁 127.0.0.1 會讓容器內看起來完全正常,
#    但 gateway 一律連不上(這種錯的症狀是 502,而容器 healthy)。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
