# Web app image. Build from the repo root; docker-compose.prod.yml passes the region. NEXT_PUBLIC_* values
# are written into the browser code at build time.
FROM node:25-alpine@sha256:bdf2cca6fe3dabd014ea60163eca3f0f7015fbd5c7ee1b0e9ccb4ced6eb02ef4 AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/scripts ./scripts
RUN npm ci
COPY frontend ./
ARG NEXT_PUBLIC_REGION_BBOX
ENV NEXT_OUTPUT=standalone NEXT_TELEMETRY_DISABLED=1
RUN test -n "$NEXT_PUBLIC_REGION_BBOX" && npm run build

FROM node:25-alpine@sha256:bdf2cca6fe3dabd014ea60163eca3f0f7015fbd5c7ee1b0e9ccb4ced6eb02ef4
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
