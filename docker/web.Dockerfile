# 开发阶段
FROM node:latest AS development
WORKDIR /app


ARG http_proxy
ARG https_proxy
ENV http_proxy=$http_proxy \
    https_proxy=$https_proxy \
    TZ=Asia/Shanghai

# 复制 package.json 和 package-lock.json（如果存在）
COPY ./web/package*.json ./

# 安装依赖
# 版本与 web/package.json 的 packageManager 字段一致，避免运行时 corepack/pnpm
# 再联网下载匹配版本（否则会命中无效代理导致 ERR_PNPM_META_FETCH_FAIL）
RUN npm install -g pnpm@10.11.0
# RUN pnpm install
# RUN npm install --registry http://mirrors.cloud.tencent.com/npm/ --verbose --force
RUN npm install --registry https://registry.npmmirror.com --verbose --force


# 复制源代码
COPY ./web .

# 暴露端口
EXPOSE 5173

# 启动开发服务器的命令在 docker-compose 文件中定义

# 生产阶段
FROM node:latest AS build-stage
WORKDIR /app

COPY ./web/package*.json ./
RUN npm install --force
RUN npm install --registry https://registry.npmmirror.com --force

COPY ./web .
RUN npm run build

# 生产环境运行阶段
FROM nginx:alpine AS production
# --from 指的是来自某个镜像 
COPY --from=build-stage /app/dist /usr/share/nginx/html
COPY ./docker/nginx/nginx.conf /etc/nginx/nginx.conf
COPY ./docker/nginx/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]