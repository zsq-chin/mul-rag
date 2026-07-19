<template>
  <div class="shi-hui-container">
    <!-- <h2 class="title"></h2> -->
    <div class="content-wrapper">
  <div class="carousel-section">
    <button class="nav-arrow prev" @click="prevSlide">
      <svg viewBox="0 0 24 24"><path d="M15.41 16.59L10.83 12l4.58-4.59L14 6l-6 6 6 6 1.41-1.41z"/></svg>
    </button>
    <div class="carousel-container">
      <transition-group name="fade" tag="div" class="carousel">
        <div v-for="(image, index) in images"
             :key="image.id"
             v-show="currentIndex === index"
             class="slide">
          <img :src="image.src" :alt="image.alt" class="promo-image">
        </div>
      </transition-group>
    </div>
    <button class="nav-arrow next" @click="nextSlide">
      <svg viewBox="0 0 24 24"><path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6-1.41-1.41z"/></svg>
    </button>
    <div class="carousel-controls">
      <button v-for="(image, index) in images"
              :key="'dot-'+image.id"
              @click="goToSlide(index)"
              :class="['dot', { active: currentIndex === index }]">
      </button>
    </div>
  </div>
      <div class="info-section">
        <h1 class="subtitle">辽河知识图谱与文档助手</h1>
        <ul class="feature-list">
          <li v-for="(feature, index) in features" :key="index" class="feature-item">
            <span class="feature-icon">✓</span>
            <span>{{ feature }}</span>
          </li>
        </ul>
        <button @click="navigateToLLM" class="action-button">
          立即体验
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue';
import { useRouter } from 'vue-router';

// 在 setup 顶部定义 router
const router = useRouter();

const images = [
  { id: 1, src: '/intro/shihui/chatImage.png', alt: 'Guide' },
  { id: 2, src: '/intro/shihui/graph.png', alt: 'Chat' },
  { id: 3, src: '/intro/shihui/know.png', alt: 'Knowledge Base' }
];

const features = [

];

const currentIndex = ref(0);
let interval = null;

const nextSlide = () => {
  currentIndex.value = (currentIndex.value + 1) % images.length;
  resetTimer();
};

const prevSlide = () => {
  currentIndex.value = (currentIndex.value - 1 + images.length) % images.length;
  resetTimer();
};

const goToSlide = (index) => {
  currentIndex.value = index;
  resetTimer();
};

const resetTimer = () => {
  clearInterval(interval);
  startCarousel();
};

const startCarousel = () => {
  interval = setInterval(nextSlide, 1500);
};

onMounted(() => {
  startCarousel();
});

onBeforeUnmount(() => {
  clearInterval(interval);
});

const navigateToLLM = () => {
    router.push('/login');
};
</script>

<style scoped>
.shi-hui-container {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: center;
  margin: 0 auto;
  padding: 2rem;
}

.title {
  color: #d32f2f;
  font-size: 2.5rem;
  text-align: center;
  margin-bottom: 2rem;
  position: relative;
  padding-bottom: 1rem;
}

.title::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 80px;
  height: 3px;
  background-color: #d32f2f;
}

.content-wrapper {
  display: flex;
  gap: 3rem;
  align-items: center;
}

.carousel-section {
  flex: 2;
}

.info-section {
  flex: 1;
  padding: 2rem;
  padding-top: 0;
}

.carousel-section {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 0 40px;
}

.carousel-container {
  width: 90%;
  height: 55vh;
  overflow: hidden;
  border-radius: 12px;
  box-shadow: 0 12px 24px rgba(138, 132, 132, 0.4);
  margin: 0 auto;
}

.carousel {
  position: relative;
  width: 100%;
  height: 100%;
}

.slide {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
}

.promo-image {
  width: 100%;
  height: 100%;
  /* object-fit: cover; */
  transition: transform 0.8s ease;
}

.promo-image:hover {
  transform: scale(1.03);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.8s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.nav-arrow {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 48px;
  height: 48px;
  background-color: rgba(255, 255, 255, 0.8);
  border: none;
  border-radius: 50%;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
  transition: all 0.3s ease;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.nav-arrow:hover {
  background-color: white;
  transform: translateY(-50%) scale(1.1);
}

.nav-arrow svg {
  width: 24px;
  height: 24px;
  fill: #d32f2f;
}

.prev {
  left: 0px;
}

.next {
  right: 0px;
}

.carousel-controls {
  margin-top: 20px;
  display: flex;
  gap: 10px;
  justify-content: center;
}

.dot {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background-color: rgba(0, 0, 0, 0.2);
  border: none;
  cursor: pointer;
  transition: all 0.3s ease;
}

.dot.active {
  background-color: #d32f2f;
  transform: scale(1.2);
}

.dot:hover {
  background-color: rgba(211, 47, 47, 0.8);
}

.subtitle {
  /* color: rgb(207, 49, 49); */
  /* font-weight: bold; */
  font-size: 35px;
  margin-bottom: 1.5rem;
  position: relative;
  padding-bottom: 0.5rem;
}

.subtitle::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  width: 60px;
  height: 2px;
  background-color: #d32f2f;
}

.feature-list {
  list-style: none;
  padding: 0;
  margin-bottom: 2rem;
}

.feature-item {
  display: flex;
  align-items: center;
  margin-bottom: 1rem;
  font-size: 1.1rem;
  color: #444;
  transition: all 0.3s ease;
}

.feature-item:hover {
  color: #d32f2f;
  transform: translateX(5px);
}

.feature-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  background-color: #d32f2f;
  color: white;
  border-radius: 50%;
  margin-right: 12px;
  font-size: 0.9rem;
}

.action-button {
  padding: 1rem 2.5rem;
  background-color: #d32f2f;
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 1.2rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.3s ease;
  box-shadow: 0 4px 12px rgba(211, 47, 47, 0.3);
  /* display: block; */
  width: 100%;
  max-width: 300px;
  margin: 0 auto;
}

.action-button:hover {
  background-color: #b71c1c;
  transform: translateY(-3px);
  box-shadow: 0 6px 16px rgba(211, 47, 47, 0.4);
}

@media (max-width: 992px) {
  .content-wrapper {
    flex-direction: column;
  }

  .info-section {
    padding: 2rem 0;
  }
}
</style>
