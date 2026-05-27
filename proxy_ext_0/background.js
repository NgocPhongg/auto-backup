chrome.webRequest.onAuthRequired.addListener(
  function(details, callbackFn) {
    callbackFn({
      authCredentials: {
        username: "viproxy",
        password: "PYoCcJnmSr"
      }
    });
  },
  { urls: ["<all_urls>"] },
  ["asyncBlocking"]
);