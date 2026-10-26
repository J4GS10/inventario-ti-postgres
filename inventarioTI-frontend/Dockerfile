# =========================================================================
# Frontend de PRODUCCIÓN — Vite build + Nginx con security headers
# =========================================================================

# --------- Stage 1: build ---------
FROM node:20-alpine AS builder

WORKDIR /app

# Solo manifest primero para aprovechar caché de capa
COPY package*.json ./
RUN npm ci --no-audit --no-fund

# Resto del código
COPY . .

# Variables Vite (se inyectan al hacer build)
ARG VITE_API_URL=/api/v1
ARG VITE_SESSION_IDLE_TIMEOUT_MINUTES=30
ENV VITE_API_URL=${VITE_API_URL}
ENV VITE_SESSION_IDLE_TIMEOUT_MINUTES=${VITE_SESSION_IDLE_TIMEOUT_MINUTES}

RUN npm run build

# --------- Stage 2: runtime (Nginx) ---------
# >= 1.27.3: `server ... resolve` en upstream (re-resolución DNS del backend).
FROM nginx:1.27.5-alpine AS runtime

# Headers de seguridad, gzip, fallback SPA, proxy al backend
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Static build
COPY --from=builder /app/dist /usr/share/nginx/html

# Nginx ya corre como nginx (no-root) en imágenes oficiales recientes
EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --spider --tries=1 http://127.0.0.1/healthz || exit 1
