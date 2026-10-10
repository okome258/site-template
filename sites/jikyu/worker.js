// 静的サイトの前に置く小さな Worker。
// 旧URL(*.workers.dev)に来たアクセスを、正式なドメイン(CANONICAL_HOST)へ恒久転送(301)する。
// それ以外は public/ のファイルをそのまま返す。
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const canonical = env.CANONICAL_HOST;
    if (canonical && url.hostname !== canonical && url.hostname.endsWith(".workers.dev")) {
      url.hostname = canonical;
      url.protocol = "https:";
      url.port = "";
      return Response.redirect(url.toString(), 301);
    }
    return env.ASSETS.fetch(request);
  },
};
