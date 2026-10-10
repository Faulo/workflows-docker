ARG BASE_IMAGE=mcr.microsoft.com/windows/nanoserver:ltsc2022
FROM ${BASE_IMAGE} AS build
USER ContainerAdministrator
COPY common/message.txt C:/app/message.txt
RUN copy C:\app\message.txt C:\app\built.txt >NUL

FROM ${BASE_IMAGE}
USER ContainerAdministrator
COPY --from=build C:/app/built.txt C:/app/shared.txt
ARG VARIANT
ARG TARGET_ID
ARG SHARED_MESSAGE
LABEL workflow.fixture.variant="${VARIANT}" \
      workflow.fixture.target="${TARGET_ID}" \
      workflow.fixture.shared="${SHARED_MESSAGE}"
RUN echo %VARIANT%> C:\app\variant.txt
CMD ["cmd", "/S", "/C", "type C:\\app\\shared.txt C:\\app\\variant.txt"]
