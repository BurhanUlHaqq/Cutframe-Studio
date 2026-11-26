function requestError(status, data) {
  const error = new Error(data?.error || (
    status === 404 ? "This render is unavailable in this browser." :
    status >= 500 ? "The studio API is unavailable. Try again shortly." :
    "The studio could not complete this request."
  ));
  error.fields = data?.fields || {};
  error.status = status;
  return error;
}

export async function api(path, options = {}, responseType) {
  let response;
  try {
    const endpoint = path.startsWith("/api/") ? path : `/api/${path}`;
    response = await fetch(endpoint, { cache: "no-store", ...options });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error("The studio could not reach the API. Check that the containers are running.");
  }
  if (response.ok && responseType === "blob") return response.blob();
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw requestError(response.status, data);
  return data;
}

export async function submit(path, payload, responseType) {
  const session = await api("session/");
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": session.csrf_token },
    body: JSON.stringify(payload),
  }, responseType);
}

export async function submitUpload(video, options, onProgress) {
  const session = await api("session/");
  const body = new FormData();
  for (const [field, value] of Object.entries(options)) body.append(field, value ?? "");
  body.append("video", video);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs/upload/");
    xhr.responseType = "json";
    xhr.setRequestHeader("X-CSRFToken", session.csrf_token);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.floor(event.loaded * 100 / event.total));
    };
    xhr.onload = () => {
      if (xhr.status === 201 && xhr.response?.id) resolve(xhr.response);
      else reject(requestError(xhr.status, xhr.response));
    };
    xhr.onerror = xhr.onabort = () => reject(new Error("The upload was interrupted. Choose the video and try again."));
    xhr.send(body);
  });
}

export const stages = [
  ["download", "Get source video"],
  ["transcribe", "Transcribe audio"],
  ["rank", "Find highlights"],
  ["render", "Render clips"],
  ["export", "Package the ZIP"],
];

export const statusLabels = {
  queued: "Queued",
  running: "Processing",
  cancelling: "Stopping",
  cancelled: "Cancelled",
  complete: "Ready",
  failed: "Failed",
  deleting: "Cleanup incomplete",
};
