# Build the bundle on Linux (glibc: the lockfile's native bindings have no musl build for TypeScript).
FROM node:22-slim AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Serve it with nginx as a non-root user on 8080; /api is proxied to API_UPSTREAM, rendered at container start.
FROM nginxinc/nginx-unprivileged:1.29-alpine
# Use the container's own DNS servers, so the upstream name is re-resolved when the API's address changes.
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1
COPY --chmod=0755 nginx/entrypoint.d/05-require-api-upstream.sh /docker-entrypoint.d/
COPY nginx/templates/ /etc/nginx/templates/
COPY nginx/snippets/ /etc/nginx/snippets/
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 8080
