FROM node:22-alpine AS deps
WORKDIR /srv
COPY package.json package-lock.json* ./
RUN npm ci || npm install

FROM node:22-alpine AS build
WORKDIR /srv
COPY --from=deps /srv/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine
WORKDIR /srv
ENV NODE_ENV=production
COPY --from=build /srv/.next ./.next
COPY --from=build /srv/node_modules ./node_modules
COPY --from=build /srv/package.json ./package.json
COPY --from=build /srv/public ./public
EXPOSE 3000
CMD ["npm", "start"]
