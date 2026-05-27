// Proxy credentials sẽ được inject bởi Python trước khi load extension
// File này là template, Python sẽ tạo file thực tế với user/pass cụ thể
chrome.webRequest.onAuthRequired.addListener(
  function(details) {
    return {
      authCredentials: {
        username: "PROXY_USER",
        password: "PROXY_PASS"
      }
    };
  },
  { urls: ["<all_urls>"] },
  ["blocking"]
);
