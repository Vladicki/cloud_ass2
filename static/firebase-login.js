// import { initializeApp } from "firebase/app";
// import { getAnalytics } from "firebase/analytics";
// TODO: Add SDKs for Firebase products that you want to use
// https://firebase.google.com/docs/web/setup#available-libraries
import { initializeApp } from "https://www.gstatic.com/firebasejs/12.9.0/firebase-app.js";
import {
  getAuth,
  createUserWithEmailAndPassword,
  signInWithEmailAndPassword,
  signOut,
  onAuthStateChanged,
  setPersistence,
  browserLocalPersistence,
} from "https://www.gstatic.com/firebasejs/12.9.0/firebase-auth.js";

const firebaseConfig = {
  apiKey: window.FIREBASE_API_KEY,
  authDomain: "vla-csp.firebaseapp.com",
  projectId: "vla-csp",
  storageBucket: "vla-csp.firebasestorage.app",
  messagingSenderId: "971806234169",
  appId: "1:971806234169:web:fcebf0aa72d2fad0d59fe8",
  measurementId: "G-TDSHV4P42G",
};

window.addEventListener("load", async function () {
  console.log("window load handler called");
  const app = initializeApp(firebaseConfig);
  const auth = getAuth(app);
  await setPersistence(auth, browserLocalPersistence);
  updateUI(document.cookie);
  console.log("hello world load");
  console.log(document.cookie);

  onAuthStateChanged(auth, async function (user) {
    console.log("auth state changed", !!user);

    const cookieToken = parseCookieToken(document.cookie);

    if (!user) {
      if (cookieToken.length > 0) {
        document.cookie = "token=;path=/;SameSite=Strict;Max-Age=0";
        window.location = "/";
        return;
      }

      updateUI(document.cookie);
      return;
    }

    const token = await user.getIdToken();

    if (cookieToken !== token) {
      document.cookie = "token=" + token + ";path=/;SameSite=Strict";
      window.location = "/";
      return;
    }

    updateUI(document.cookie);
  });

  const openSignUpButton = document.getElementById("open-sign-up");
  const signUpSubmitButton = document.getElementById("sign-up-submit");
  const backToLoginButton = document.getElementById("back-to-login");
  const loginButton = document.getElementById("login");
  const signOutButton = document.getElementById("sign-out");

  if (openSignUpButton) {
    openSignUpButton.addEventListener("click", function () {
      const loginBox = document.getElementById("login-box");
      const signUpBox = document.getElementById("signup-box");
      if (loginBox) loginBox.hidden = true;
      if (signUpBox) signUpBox.hidden = false;
    });
  }

  if (backToLoginButton) {
    backToLoginButton.addEventListener("click", function () {
      const loginBox = document.getElementById("login-box");
      const signUpBox = document.getElementById("signup-box");
      if (signUpBox) signUpBox.hidden = true;
      if (loginBox) loginBox.hidden = false;
    });
  }

  // signup of a new user to firebase
  if (signUpSubmitButton) {
    signUpSubmitButton.addEventListener("click", function () {
      console.log("sign-up handler called");
      const email = document.getElementById("sign-up-email").value;
      const password = document.getElementById("sign-up-password").value;

      createUserWithEmailAndPassword(auth, email, password)
        .then((userCredential) => {
          console.log("sign-up success callback called");
          const user = userCredential.user;

          user.getIdToken().then((token) => {
            console.log("sign-up token callback called");
            document.cookie = "token=" + token + ";path=/;SameSite=Strict";
            window.location = "/";
          });
        })
        .catch((error) => {
          console.log("sign-up error callback called");
          console.log(error.code + error.message);
        });
    });
  }

  // login of a user to firebase
  if (loginButton) {
    loginButton.addEventListener("click", function () {
      console.log("login handler called");
      const email = document.getElementById("email").value;
      const password = document.getElementById("password").value;

      signInWithEmailAndPassword(auth, email, password)
        .then((userCredential) => {
          console.log("login success callback called");
          // we have a signed in user
          const user = userCredential.user;
          console.log("logged in");

          // get the id token for the user who just logged in and force a redirect to /
          user.getIdToken().then((token) => {
            console.log("login token callback called");
            document.cookie = "token=" + token + ";path=/;SameSite=Strict";
            console.log(document.cookie);
            window.location = "/";
          });
        })
        .catch((error) => {
          console.log("login error callback called");
          // issue with signup that we will drop to console
          console.log(error.code + error.message);
        });
    });
  }

  if (signOutButton) {
    signOutButton.addEventListener("click", function () {
      console.log("sign-out handler called");
      signOut(auth).then((output) => {
        console.log("sign-out success callback called");
        document.cookie = "token=;path=/;SameSite=Strict";
        window.location = "/";
        console.log(document.cookie);
      });
    });
  }
});

function updateUI(cookie) {
  console.log("updateUI called", cookie);
  var token = parseCookieToken(cookie);
  const loginBox = document.getElementById("login-box");
  const signUpBox = document.getElementById("signup-box");
  const signOutButton = document.getElementById("sign-out");

  if (loginBox) {
    loginBox.hidden = token.length > 0;
  }

  if (signUpBox) {
    signUpBox.hidden = true;
  }

  if (signOutButton) {
    signOutButton.hidden = token.length === 0;
  }
}
// function that will take the cookie and will return the value associated with it to the caller
function parseCookieToken(cookie) {
  console.log("parseCookieToken called", cookie);
  // split the cookie out on the basis of the semi-colon
  var strings = cookie.split(";");

  // go through each of the strings
  for (let i = 0; i < strings.length; i++) {
    // split the string based on the = sign. if the LHS is token then return the RHS immediately
    var temp = strings[i].split("=");
    if (temp[0] == "token") return temp[1];
  }

  // if we get to this point then the token wasn't in the cookie so return the empty string
  return "";
}
