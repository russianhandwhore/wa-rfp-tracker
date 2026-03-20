/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./pages/**/*.{js,jsx}",
    "./components/**/*.{js,jsx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          red: "#EE0000",
          darkred: "#CC0000",
          black: "#151515",
          darkgray: "#212427",
          gray: "#6A6E73",
          lightgray: "#F5F5F5",
        }
      }
    },
  },
  plugins: [],
}
