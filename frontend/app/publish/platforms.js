import { Instagram, Music2, Youtube } from "lucide-react";

export const platforms = {
  youtube: {
    name: "YouTube",
    Icon: Youtube,
    upload: "https://studio.youtube.com/",
    idLabel: "YouTube OAuth client ID",
    secretLabel: "YouTube OAuth client secret",
    instructions: [
      "Enable YouTube Data API v3 in a Google Cloud project.",
      "Configure OAuth consent and add your account as a test user if the project is in testing.",
      "Create a Web application OAuth client and register the callback shown below.",
    ],
    note: "Use Private for your first upload. Google project verification and YouTube API policies apply.",
    docs: "https://developers.google.com/identity/protocols/oauth2/web-server",
  },
  tiktok: {
    name: "TikTok",
    Icon: Music2,
    upload: "https://www.tiktok.com/tiktokstudio/upload",
    idLabel: "TikTok client key",
    secretLabel: "TikTok client secret",
    instructions: [
      "Create a TikTok developer app with Login Kit for Web and the Content Posting API.",
      "Request video.upload and user.info.basic access. Use your client key as the client ID.",
      "Register the HTTPS callback, then authorize the account that will receive drafts.",
    ],
    note: "This sends an inbox draft. Open the TikTok notification to review and finish the post.",
    docs: "https://developers.tiktok.com/docs/en/content-posting-api-get-started-upload-content",
  },
  instagram: {
    name: "Instagram Reels",
    Icon: Instagram,
    upload: "https://www.instagram.com/",
    idLabel: "Meta app ID",
    secretLabel: "Meta app secret",
    instructions: [
      "Link an Instagram Business or Creator account to a Facebook Page you manage.",
      "Set up Instagram API with Facebook Login for Business. Enable pages_show_list, pages_read_engagement, instagram_basic, and instagram_content_publish.",
      "Register the HTTPS callback in Facebook Login and connect the Facebook account that manages the Page.",
      "For a Facebook Login for Business configuration, save its User access token configuration ID in Settings → Publishing.",
    ],
    note: "Choose the professional Instagram account you want to publish to. Meta app roles or approval may be required.",
    docs: "https://developers.facebook.com/documentation/instagram-platform/content-publishing",
  },
};
