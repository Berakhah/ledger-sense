import type { NextConfig } from 'next'

const config: NextConfig = {
  // The API is a separate service (see docker-compose.yml). The browser talks
  // to it directly at NEXT_PUBLIC_API_URL; nothing is proxied through Next,
  // so the session cookie keeps one origin and SameSite=strict keeps meaning
  // what it says.
  reactStrictMode: true,
}

export default config
