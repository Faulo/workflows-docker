FROM alpine:3 AS build
COPY common/message.txt /app/message.txt
RUN cp /app/message.txt /app/built.txt

FROM alpine:3
COPY --from=build /app/built.txt /app/shared.txt
ARG VARIANT
ARG TARGET_ID
ARG SHARED_MESSAGE
LABEL workflow.fixture.variant="${VARIANT}" \
      workflow.fixture.target="${TARGET_ID}" \
      workflow.fixture.shared="${SHARED_MESSAGE}"
RUN printf '%s\n' "$VARIANT" > /app/variant.txt
CMD ["sh", "-c", "cat /app/shared.txt /app/variant.txt"]
