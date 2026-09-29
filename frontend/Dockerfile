FROM node:20-alpine AS build
WORKDIR /app

# npm ci exige o package-lock.json e instala exatamente as versoes travadas,
# garantindo que o build local e o build do container sejam identicos.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.conf /etc/nginx/security-headers.conf
COPY --from=build /app/dist/rag-saude-local /usr/share/nginx/html
EXPOSE 80
