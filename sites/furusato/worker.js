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
    // 楽天の商品画像を自サイト経由で配信(直リンクだと表示されないため)。楽天の画像サーバーだけ許可
    // 楽天の商品画像を自サイト経由で配信(直リンクや広告ブロックで表示されないため)。
    // /i/<楽天の画像パス>?<クエリ> → https://thumbnail.image.rakuten.co.jp/<画像パス>
    if (url.pathname.startsWith("/i/")) {
      const target = "https://thumbnail.image.rakuten.co.jp/" + url.pathname.slice(3) + url.search;
      const res = await fetch(target, {
        headers: { "User-Agent": "Mozilla/5.0" },
        cf: { cacheTtl: 86400, cacheEverything: true },
      });
      if (!res.ok) return new Response("not found", { status: 404 });
      const h = new Headers();
      h.set("Content-Type", res.headers.get("Content-Type") || "image/jpeg");
      h.set("Cache-Control", "public, max-age=86400");
      return new Response(res.body, { status: 200, headers: h });
    }
    return env.ASSETS.fetch(request);
  },
};
