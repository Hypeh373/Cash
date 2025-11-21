const navToggle = document.querySelector(".nav__toggle");
const navList = document.querySelector(".nav__list");
const animateTargets = document.querySelectorAll("[data-animate]");
const galleryForm = document.querySelector("[data-gallery-form]");
const galleryGrid = document.querySelector("[data-gallery-grid]");
const galleryTemplate = document.getElementById("gallery-card-template");
const formStatus = galleryForm?.querySelector(".form-status");
const fileInput = galleryForm?.querySelector('input[name="photo"]');
const fileLabel = galleryForm?.querySelector("[data-file-label]");

/* ---------- Навигация ---------- */
navToggle?.addEventListener("click", () => {
  navList?.classList.toggle("is-open");
});

document.addEventListener("click", (event) => {
  if (
    navList?.classList.contains("is-open") &&
    !navList.contains(event.target) &&
    event.target !== navToggle
  ) {
    navList.classList.remove("is-open");
  }
});

/* ---------- Анимации появления ---------- */
const revealObserver =
  typeof IntersectionObserver !== "undefined"
    ? new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) {
              entry.target.classList.add("is-visible");
              revealObserver.unobserve(entry.target);
            }
          });
        },
        {
          threshold: 0.2,
          rootMargin: "0px 0px -80px 0px",
        }
      )
    : null;

animateTargets.forEach((target) => revealObserver?.observe(target));

/* ---------- Галерея и локальное хранилище ---------- */
const STORAGE_KEY = "starogreen-gallery";

const storageAvailable = (() => {
  try {
    const testKey = "__storage_test__";
    localStorage.setItem(testKey, testKey);
    localStorage.removeItem(testKey);
    return true;
  } catch (error) {
    console.warn("LocalStorage недоступно:", error);
    return false;
  }
})();

const savedEntries = storageAvailable
  ? JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]")
  : [];

savedEntries.forEach((entry) => appendGalleryCard(entry));

fileInput?.addEventListener("change", () => {
  if (fileInput.files?.[0]) {
    fileLabel.textContent = fileInput.files[0].name;
  } else {
    fileLabel.textContent = "Файл не выбран";
  }
});

galleryForm?.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!fileInput?.files?.length) {
    updateStatus("Добавьте фото, прежде чем публиковать", true);
    return;
  }

  const title = galleryForm.title.value.trim();
  const description = galleryForm.description.value.trim();
  const category = galleryForm.category.value;
  const photo = fileInput.files[0];

  if (!title || !description || !category) {
    updateStatus("Заполните все поля формы", true);
    return;
  }

  updateStatus("Обрабатываем фотографию...");

  try {
    const dataUrl = await readFile(photo);

    const newEntry = {
      id: crypto?.randomUUID?.() ?? Date.now().toString(),
      title,
      description,
      category,
      image: dataUrl,
      createdAt: new Date().toISOString(),
    };

    appendGalleryCard(newEntry, true);

    if (storageAvailable) {
      savedEntries.push(newEntry);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(savedEntries));
    }

    galleryForm.reset();
    fileLabel.textContent = "Файл не выбран";
    updateStatus("Фото опубликовано! Обновите страницу, чтобы увидеть его снова.", false);
  } catch (error) {
    console.error(error);
    updateStatus("Не удалось обработать файл. Попробуйте другое изображение.", true);
  }
});

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function appendGalleryCard(entry, highlight = false) {
  if (!galleryTemplate || !galleryGrid) return;

  const clone = galleryTemplate.content.cloneNode(true);
  const figure = clone.querySelector(".gallery-card");
  const img = clone.querySelector("img");
  const tag = clone.querySelector(".gallery-card__tag");
  const titleNode = clone.querySelector("h3");
  const meta = clone.querySelector(".gallery-card__meta");
  const descriptionNode = clone.querySelector("figcaption p:last-child");

  if (!img || !tag || !titleNode || !meta || !descriptionNode) return;

  img.src = entry.image;
  img.alt = entry.title;
  tag.textContent = entry.category;
  titleNode.textContent = entry.title;
  meta.textContent = `Новая публикация • ${formatDate(entry.createdAt)}`;
  descriptionNode.textContent = entry.description;

  if (highlight) {
    figure.classList.add("is-new");
    setTimeout(() => figure.classList.remove("is-new"), 3000);
  }

  galleryGrid.prepend(clone);
}

function formatDate(dateString) {
  try {
    return new Intl.DateTimeFormat("ru-RU", {
      day: "numeric",
      month: "long",
      year: "numeric",
    }).format(new Date(dateString));
  } catch {
    return "";
  }
}

function updateStatus(message, isError = false) {
  if (!formStatus) return;
  formStatus.textContent = message;
  formStatus.style.color = isError ? "#ff9f9f" : "var(--accent-light)";
}
