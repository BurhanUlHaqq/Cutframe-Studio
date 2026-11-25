const backend = process.env.DJANGO_API_URL || "http://127.0.0.1:5032";

export default {
  output: "standalone",
  poweredByHeader: false,
  skipTrailingSlashRedirect: true,
  async rewrites() {
    return [
      { source: "/api/:path*/", destination: `${backend}/api/:path*/` },
    ];
  },
};
