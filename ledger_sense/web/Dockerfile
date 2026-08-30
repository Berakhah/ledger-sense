# The Next.js service. Built from the repo root, not from this directory:
# there is one package.json and one lockfile for the whole node side (see
# package.json's description), and the app is passed to next as its project
# directory.

FROM node:22-alpine AS build
WORKDIR /app
# Lockfile first, so a source-only change does not re-resolve the tree.
COPY package.json package-lock.json ./
RUN npm ci
COPY scripts/copy-pdf-worker.mjs ./scripts/
COPY ledger_sense/web ./ledger_sense/web
# `prebuild` copies the pdf.js worker out of node_modules into public/. No
# CDN: the demo has to run with no network. See scripts/copy-pdf-worker.mjs.
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/ledger_sense/web ./ledger_sense/web
# The node image ships an unprivileged `node` user. Same rule as the Python
# Dockerfile: nothing in this stack runs as root.
USER node
EXPOSE 3000
CMD ["npm", "start"]
